"""Durable, route-aware tracing for every LLM request.

The web observability page consumes the ``llm_calls`` schema directly.  This
module is deliberately independent from ADK and the HTTP client so both MAS
and City/Program classification produce the same record shape.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from google.adk.agents import LlmAgent

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

logger = logging.getLogger("llm_trace")

_trace_id = contextvars.ContextVar("llm_trace_id", default=None)
_trigger = contextvars.ContextVar("llm_trace_trigger", default="unknown")
_route = contextvars.ContextVar("llm_trace_route", default="unknown")
_page_id = contextvars.ContextVar("llm_trace_page_id", default=None)
_subject = contextvars.ContextVar("llm_trace_subject", default=None)
_dry_run = contextvars.ContextVar("llm_trace_dry_run", default=True)
_seq_counter = contextvars.ContextVar("llm_trace_seq", default=0)
_attempt = contextvars.ContextVar("llm_trace_attempt", default=1)
_last_call_id = contextvars.ContextVar("llm_trace_last_call_id", default=None)
_last_call_trace_id = contextvars.ContextVar("llm_trace_last_call_trace_id", default=None)

_SUBJECT_REQUIRED_ROUTES = {
    "propose", "reply", "warmup", "event", "react", "stage_gate", "recommend",
    "classify", "classify_detect", "classify_verify",
}

_AGENT_ROUTE_DEFAULTS = {
    "MessageClassifier": "propose",
    "Responder": "propose",
    "ConversationAnalyst": "propose",
    "KnowledgeLibrarian": "propose",
    "ReplyComposer": "propose",
    "ReplyQAReviewer": "propose",
    "ReplyRepair": "propose",
    "Reactor": "react",
    "WarmUpComposer": "warmup",
    "EventAdvertiser": "event",
    "city_llm": "classify_detect",
}


def get_trace_id() -> Optional[str]:
    """Return the current trace id for callers that need to join a trace."""
    return _trace_id.get()


def get_trigger() -> str:
    """Return the current trigger, useful when a helper joins an outer span."""
    return _trigger.get() or "unknown"


def get_trace_context() -> dict[str, Any]:
    """Return the current MAS action identity for an ADK session.

    ADK can execute callbacks in a task whose :mod:`contextvars` do not
    inherit the caller's span.  Session state is the durable boundary shared
    by every agent in an action, so callers pass this small internal payload
    when they create an ADK session.
    """
    subject = _subject.get()
    return {
        "trace_id": _trace_id.get(),
        "trigger": _trigger.get() or "unknown",
        "route": _route.get() or "unknown",
        "page_id": _page_id.get(),
        "subject": list(subject) if subject else None,
        "dry_run": _dry_run.get(),
    }


def _get_route_group(route: str) -> str:
    return "LLM" if route.startswith("classify") or route in {"city_detect", "city_verify"} else "MAS"


def _jsonable(value: Any) -> Any:
    """Convert ADK/Pydantic/GenAI objects to JSON-safe data without failing a trace."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    for method in ("model_dump", "to_dict", "dict"):
        converter = getattr(value, method, None)
        if callable(converter):
            try:
                return _jsonable(converter())
            except Exception:
                pass
    if hasattr(value, "__dict__"):
        try:
            return _jsonable(vars(value))
        except Exception:
            pass
    return str(value)


def _json_dumps(value: Any, fallback: str = "null") -> str:
    try:
        return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)
    except Exception as exc:
        logger.warning("Could not serialize LLM trace payload: %s", exc)
        return fallback


def _strip_internal_state(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_internal_state(item)
            for key, item in value.items()
            if not str(key).startswith("_llm_trace_")
        }
    if isinstance(value, list):
        return [_strip_internal_state(item) for item in value]
    return value


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            if key in value:
                text = _as_text(value[key])
                if text:
                    return text
    parts = getattr(value, "parts", None)
    if parts:
        # A function-call part intentionally has no user-facing text. Never
        # stringify its SDK object into a fake assistant reply.
        return "".join(_as_text(getattr(part, "text", "")) for part in parts)
    return str(value)


def _extract_function_calls(llm_response: Any, content: Any) -> list[Any]:
    """Return tool requests across the ADK/GenAI response shapes we support."""
    calls = list(getattr(llm_response, "function_calls", None) or [])
    for part in getattr(content, "parts", None) or []:
        call = getattr(part, "function_call", None)
        if call is not None:
            calls.append(call)
    # Preserve order while avoiding the common case where ADK exposes the same
    # call both in ``response.function_calls`` and ``content.parts``.
    unique: list[Any] = []
    seen: set[str] = set()
    for call in calls:
        key = _json_dumps(call, "")
        if key and key not in seen:
            seen.add(key)
            unique.append(call)
    return unique


def _append_tool_event(call_id: Optional[int], event: dict[str, Any]) -> None:
    """Attach one tool lifecycle event to its originating model call.

    ``llm_calls`` remains the canonical one-row-per-model-call table. Tool
    events live in its JSON response payload so existing trace grouping and
    parent links stay intact while /llm can render a chronological tool log.
    """
    if not call_id:
        return
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT response_json FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
        raw = row[0] if row else None
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, json.JSONDecodeError):
            payload = {"unparseable_response_json": raw}
        events = payload.setdefault("tool_calls", [])
        events.append(_jsonable(event))
        conn.execute("UPDATE llm_calls SET response_json = ? WHERE id = ?", (_json_dumps(payload, "{}"), call_id))
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to append tool trace event for call_id=%s: %s", call_id, exc)


def _safe_insert(kwargs: dict[str, Any]) -> Optional[int]:
    try:
        conn = get_db_connection()
        conn.execute("PRAGMA busy_timeout=5000;")
        columns = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        cursor = conn.execute(
            f"INSERT INTO llm_calls ({columns}) VALUES ({placeholders}) RETURNING id",
            tuple(kwargs.values()),
        )
        call_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return call_id
    except Exception as exc:
        logger.warning("Failed to insert llm trace: %s", exc)
        return None


def _safe_update(call_id: int, updates: dict[str, Any]) -> None:
    if not call_id:
        return
    try:
        conn = get_db_connection()
        conn.execute("PRAGMA busy_timeout=5000;")
        set_clause = ", ".join([f"{key} = ?" for key in updates])
        conn.execute(
            f"UPDATE llm_calls SET {set_clause} WHERE id = ?",
            tuple(updates.values()) + (call_id,),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to update llm trace for call_id=%s: %s", call_id, exc)


@contextmanager
def span(*, trigger: str, route: str, page_id: Optional[str] = None,
         subject: Optional[Tuple[str, str, Optional[str]]] = None,
         dry_run: bool = True, trace_id: Optional[str] = None):
    """Set route metadata for all LLM calls in the current execution context.

    Nested spans inherit the trace id, while allowing a child route/subject to
    describe the actual model call (for example detect → verify).
    """
    parent_trace = _trace_id.get()
    effective_trace = trace_id or parent_trace or str(uuid.uuid4())
    tokens = (
        _trace_id.set(effective_trace),
        _trigger.set(trigger or _trigger.get() or "unknown"),
        _route.set(route or _route.get() or "unknown"),
        _page_id.set(page_id if page_id is not None else _page_id.get()),
        _subject.set(subject if subject is not None else _subject.get()),
        _dry_run.set(dry_run),
        _seq_counter.set(_seq_counter.get() if parent_trace else 0),
    )
    try:
        yield effective_trace
    finally:
        for variable, token in zip(
            (_trace_id, _trigger, _route, _page_id, _subject, _dry_run, _seq_counter),
            tokens,
        ):
            variable.reset(token)


@contextmanager
def span_attempt(attempt: int):
    token = _attempt.set(attempt)
    try:
        yield
    finally:
        _attempt.reset(token)


def start_call(agent_name: str = "unknown", model: str = "unknown",
               system_prompt: str = "", messages_json: str = "[]",
               state_json: str = "{}", tools_json: Optional[str] = None,
               parent_call_id: Optional[int] = None,
               attempt: Optional[int] = None, *, trace_id: Optional[str] = None,
               trigger: Optional[str] = None, route: Optional[str] = None,
               page_id: Optional[str] = None,
               subject: Optional[Tuple[str, str, Optional[str]]] = None,
               dry_run: Optional[bool] = None,
               seq_in_trace: Optional[int] = None) -> Optional[int]:
    trace_id = trace_id or _trace_id.get() or str(uuid.uuid4())
    _trace_id.set(trace_id)
    seq = seq_in_trace if seq_in_trace is not None else _seq_counter.get() + 1
    current_attempt = attempt if attempt is not None else _attempt.get()
    if current_attempt == 1 and seq_in_trace is None:
        _seq_counter.set(seq)
    elif current_attempt != 1 and seq_in_trace is None:
        seq = _seq_counter.get()

    subject = subject if subject is not None else _subject.get()
    subject_type, subject_id, subject_label = subject or (None, None, None)
    route = route or _route.get() or "unknown"
    if route == "unknown":
        route = _AGENT_ROUTE_DEFAULTS.get(agent_name or "", route)
    if route in _SUBJECT_REQUIRED_ROUTES and not subject_id:
        subject_type = subject_type or "run"
        subject_id = f"trace:{trace_id}"
        subject_label = subject_label or f"Unscoped {route} run"

    call_id = _safe_insert({
        "trace_id": trace_id,
        "parent_call_id": parent_call_id,
        "seq_in_trace": seq,
        "attempt": current_attempt,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger or _trigger.get() or "unknown",
        "route": route,
        "route_group": _get_route_group(route),
        "agent_name": agent_name or "unknown",
        "model": model or "unknown",
        "page_id": page_id if page_id is not None else _page_id.get(),
        "subject_type": subject_type,
        "subject_id": subject_id,
        "subject_label": subject_label,
        "dry_run": _dry_run.get() if dry_run is None else dry_run,
        "system_prompt": system_prompt or "",
        "messages_json": messages_json or "[]",
        "state_json": state_json or "{}",
        "tools_json": tools_json,
        "status": "running",
    })
    _last_call_id.set(call_id)
    _last_call_trace_id.set(trace_id)
    return call_id


def end_call(call_id: Optional[int], response_text: Optional[str] = None,
             response_json: Optional[str] = None, error: Optional[str] = None,
             tokens_in: Optional[int] = None, tokens_out: Optional[int] = None) -> None:
    if not call_id:
        return
    error_text = str(error) if error else None
    if error_text:
        lowered = error_text.lower()
        status = "timeout" if "timeout" in lowered or "timed out" in lowered else "error"
    elif not response_text and not response_json:
        status = "empty"
    else:
        status = "ok"

    updates: dict[str, Any] = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "response_text": response_text,
        "response_json": response_json,
        "error": error_text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "status": status,
    }
    try:
        conn = get_db_connection()
        row = conn.execute("SELECT started_at FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
        if row and row[0]:
            started = datetime.fromisoformat(row[0])
            updates["duration_ms"] = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        conn.close()
    except Exception as exc:
        logger.warning("Could not calculate LLM call duration: %s", exc)
    _safe_update(call_id, updates)


def mark_sanitized(cleaned: str, call_id: Optional[int] = None) -> None:
    """Attach the post-sanitize value to the exact most recent model call."""
    try:
        trace_id = _trace_id.get()
        if not call_id and not trace_id:
            return
        conn = get_db_connection()
        current_id = call_id
        if not current_id and _last_call_trace_id.get() == trace_id:
            current_id = _last_call_id.get()
        if not current_id:
            row = conn.execute(
                "SELECT id FROM llm_calls WHERE trace_id = ? ORDER BY seq_in_trace DESC, id DESC LIMIT 1",
                (trace_id,),
            ).fetchone() if trace_id else None
            current_id = row[0] if row else None
        if not current_id:
            conn.close()
            return
        row = conn.execute("SELECT status FROM llm_calls WHERE id = ?", (current_id,)).fetchone()
        current_status = row[0] if row else "ok"
        status = "sanitized_empty" if not (cleaned or "").strip() else ("error" if current_status == "error" else "ok")
        conn.execute("UPDATE llm_calls SET sanitized_text = ?, status = ? WHERE id = ?", (cleaned or "", status, current_id))
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to mark sanitized LLM output: %s", exc)


def link_outcome(outcome_type: str, outcome_ref: Any) -> None:
    try:
        trace_id = _trace_id.get()
        if not trace_id:
            return
        conn = get_db_connection()
        conn.execute(
            "UPDATE llm_calls SET outcome_type = ?, outcome_ref = ? WHERE trace_id = ?",
            (outcome_type, str(outcome_ref), trace_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to link LLM outcome: %s", exc)


def adk_before_model(callback_context: Any, llm_request: Any) -> None:
    try:
        agent_name = getattr(callback_context, "agent_name", None) or "unknown"
        config = getattr(llm_request, "config", None)
        system_instruction = getattr(config, "system_instruction", "") if config else ""
        contents = getattr(llm_request, "contents", []) or []
        state = {}
        if hasattr(callback_context, "state") and hasattr(callback_context.state, "to_dict"):
            state = callback_context.state.to_dict() or {}
        action = state.get("_llm_trace_context")
        action = action if isinstance(action, dict) else {}
        action_subject = action.get("subject")
        if isinstance(action_subject, list) and len(action_subject) == 3:
            action_subject = tuple(action_subject)
        elif not isinstance(action_subject, tuple):
            action_subject = None
        parent_id = state.get("_llm_trace_last_call_id")
        next_seq = int(state.get("_llm_trace_seq") or 0) + 1
        clean_state = _strip_internal_state(state)
        model = getattr(llm_request, "model", None) or os.environ.get("ADK_MODEL", "openai/gpt-5.4")
        tools = getattr(llm_request, "tools_dict", None) or getattr(llm_request, "tools", None)
        call_id = start_call(
            agent_name=agent_name,
            model=model,
            system_prompt=_as_text(system_instruction),
            messages_json=_json_dumps(contents, "[]"),
            state_json=_json_dumps(clean_state, "{}"),
            tools_json=_json_dumps(tools) if tools else None,
            parent_call_id=parent_id,
            trace_id=action.get("trace_id"),
            trigger=action.get("trigger"),
            route=action.get("route"),
            page_id=action.get("page_id"),
            subject=action_subject,
            dry_run=action.get("dry_run"),
            seq_in_trace=next_seq if action.get("trace_id") else None,
        )
        if hasattr(callback_context, "state"):
            callback_context.state["_llm_trace_call_id"] = call_id
            callback_context.state["_llm_trace_last_call_id"] = call_id
            if action.get("trace_id"):
                callback_context.state["_llm_trace_seq"] = next_seq
    except Exception as exc:
        logger.warning("adk_before_model failed: %s\n%s", exc, traceback.format_exc())


def adk_after_model(callback_context: Any, llm_response: Any) -> None:
    try:
        call_id = callback_context.state.get("_llm_trace_call_id") if hasattr(callback_context, "state") else None
        if not call_id:
            return
        content = getattr(llm_response, "content", None)
        response_text = _as_text(content)
        usage = getattr(llm_response, "usage_metadata", None)
        tokens_in = getattr(usage, "prompt_token_count", None) if usage else None
        tokens_out = getattr(usage, "candidates_token_count", None) if usage else None
        error = getattr(llm_response, "error", None)
        function_calls = _extract_function_calls(llm_response, content)
        response_payload = {}
        if response_text:
            response_payload["content"] = _jsonable(content)
        if usage is not None:
            response_payload["usage"] = _jsonable(usage)
        if function_calls:
            # A function-call turn legitimately carries no display text. Keep
            # the requested calls explicit so it is never mistaken for an
            # empty model answer in the observability UI.
            response_payload["function_calls"] = _jsonable(function_calls)
            response_payload["tool_calls"] = [
                {"phase": "requested", "tool": _jsonable(call)}
                for call in function_calls
            ]
        end_call(
            call_id=call_id,
            response_text=response_text,
            response_json=_json_dumps(response_payload) if response_payload else None,
            error=str(error) if error else None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
    except Exception as exc:
        logger.warning("adk_after_model failed: %s\n%s", exc, traceback.format_exc())


def adk_before_tool(tool: Any, args: dict[str, Any], tool_context: Any) -> None:
    """Record the exact tool invocation requested by an ADK agent."""
    try:
        state = getattr(tool_context, "state", None)
        call_id = state.get("_llm_trace_call_id") if state is not None else None
        _append_tool_event(call_id, {
            "phase": "started",
            "tool_name": getattr(tool, "name", type(tool).__name__),
            "arguments": args or {},
        })
    except Exception as exc:
        logger.warning("adk_before_tool failed: %s", exc)
    return None


def adk_after_tool(tool: Any, args: dict[str, Any], tool_context: Any,
                   tool_response: Any = None, result: Any = None) -> None:
    """Record a tool result without coupling the MAS run to an ADK callback name.

    ADK currently invokes this callback using the ``tool_response`` keyword.
    Older releases (and a few local tests) pass the fourth argument as
    ``result``.  Accept both: observability must never abort the parent agent
    after a specialist has completed successfully.
    """
    try:
        response = tool_response if tool_response is not None else result
        state = getattr(tool_context, "state", None)
        call_id = state.get("_llm_trace_call_id") if state is not None else None
        _append_tool_event(call_id, {
            "phase": "completed",
            "tool_name": getattr(tool, "name", type(tool).__name__),
            "arguments": args or {},
            "result": response if response is not None else {},
        })
    except Exception as exc:
        logger.warning("adk_after_tool failed: %s", exc)
    return None


def adk_on_tool_error(tool: Any, args: dict[str, Any], tool_context: Any, error: Exception) -> None:
    """Keep failed calls visible rather than turning them into blank turns."""
    try:
        state = getattr(tool_context, "state", None)
        call_id = state.get("_llm_trace_call_id") if state is not None else None
        _append_tool_event(call_id, {
            "phase": "error",
            "tool_name": getattr(tool, "name", type(tool).__name__),
            "arguments": args or {},
            "error": str(error),
        })
    except Exception as exc:
        logger.warning("adk_on_tool_error failed: %s", exc)
    return None


def traced(agent: LlmAgent) -> LlmAgent:
    """Attach the shared before/after callbacks to an ADK agent."""
    agent.before_model_callback = adk_before_model
    agent.after_model_callback = adk_after_model
    agent.before_tool_callback = adk_before_tool
    agent.after_tool_callback = adk_after_tool
    agent.on_tool_error_callback = adk_on_tool_error
    return agent
