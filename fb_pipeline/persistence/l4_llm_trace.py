import contextvars
import json
import logging
import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timezone
import traceback
from typing import Optional, Tuple, Any

from google.adk.agents import LlmAgent
from fb_pipeline.persistence.l4_sqlite_store import get_db_connection

logger = logging.getLogger("llm_trace")

# Context variables
_trace_id = contextvars.ContextVar("llm_trace_id", default=None)
_trigger = contextvars.ContextVar("llm_trace_trigger", default="unknown")
_route = contextvars.ContextVar("llm_trace_route", default="unknown")
_page_id = contextvars.ContextVar("llm_trace_page_id", default=None)
_subject = contextvars.ContextVar("llm_trace_subject", default=None)
_dry_run = contextvars.ContextVar("llm_trace_dry_run", default=True)
_seq_counter = contextvars.ContextVar("llm_trace_seq", default=0)
_attempt = contextvars.ContextVar("llm_trace_attempt", default=1)


def _get_route_group(route: str) -> str:
    if route.startswith("classify_"):
        return "LLM"
    return "MAS"


def _safe_insert(kwargs: dict) -> Optional[int]:
    try:
        conn = get_db_connection()
        conn.execute("PRAGMA busy_timeout=5000;")
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        cursor = conn.execute(f"INSERT INTO llm_calls ({cols}) VALUES ({placeholders})", tuple(kwargs.values()))
        call_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return call_id
    except Exception as e:
        logger.warning(f"Failed to insert llm_trace: {e}")
        return None


def _safe_update(call_id: int, updates: dict):
    if not call_id:
        return
    try:
        conn = get_db_connection()
        conn.execute("PRAGMA busy_timeout=5000;")
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        conn.execute(f"UPDATE llm_calls SET {set_clause} WHERE id = ?", tuple(updates.values()) + (call_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to update llm_trace for call_id={call_id}: {e}")


@contextmanager
def span(*, trigger: str, route: str, page_id: Optional[str] = None,
         subject: Optional[Tuple[str, str, Optional[str]]] = None,
         dry_run: bool = True, trace_id: Optional[str] = None):
    """Context manager for tracing LLM calls."""
    import uuid
    token_trace_id = _trace_id.set(trace_id or _trace_id.get() or str(uuid.uuid4()))
    token_trigger = _trigger.set(trigger)
    token_route = _route.set(route)
    token_page_id = _page_id.set(page_id if page_id is not None else _page_id.get())
    token_subject = _subject.set(subject)
    token_dry_run = _dry_run.set(dry_run)
    token_seq = _seq_counter.set(0 if not _trace_id.get() else _seq_counter.get())

    try:
        yield
    finally:
        _trace_id.reset(token_trace_id)
        _trigger.reset(token_trigger)
        _route.reset(token_route)
        _page_id.reset(token_page_id)
        _subject.reset(token_subject)
        _dry_run.reset(token_dry_run)
        _seq_counter.reset(token_seq)


@contextmanager
def span_attempt(attempt: int):
    token = _attempt.set(attempt)
    try:
        yield
    finally:
        _attempt.reset(token)


def start_call(agent_name: str, model: str, system_prompt: str, messages_json: str,
               state_json: str, tools_json: str = None, parent_call_id: int = None, attempt: int = None) -> Optional[int]:
    import uuid
    t_id = _trace_id.get() or str(uuid.uuid4())
    _trace_id.set(t_id)
    
    seq = _seq_counter.get() + 1
    if attempt is None or attempt == 1:
        _seq_counter.set(seq)
    else:
        seq = _seq_counter.get()

    subj = _subject.get() or (None, None, None)
    subj_type, subj_id, subj_label = subj

    route = _route.get()
    current_attempt = attempt if attempt is not None else _attempt.get()
    
    kwargs = {
        "trace_id": t_id,
        "parent_call_id": parent_call_id,
        "seq_in_trace": seq,
        "attempt": current_attempt,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "trigger": _trigger.get(),
        "route": route,
        "route_group": _get_route_group(route),
        "agent_name": agent_name,
        "model": model,
        "page_id": _page_id.get(),
        "subject_type": subj_type,
        "subject_id": subj_id,
        "subject_label": subj_label,
        "dry_run": _dry_run.get(),
        "system_prompt": system_prompt,
        "messages_json": messages_json,
        "state_json": state_json,
        "tools_json": tools_json,
        "status": "running"
    }
    return _safe_insert(kwargs)


def end_call(call_id: int, response_text: str = None, response_json: str = None,
             error: str = None, tokens_in: int = None, tokens_out: int = None):
    if not call_id:
        return
        
    updates = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "response_text": response_text,
        "response_json": response_json,
        "error": error,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out
    }
    
    if error:
        updates["status"] = "error"
    elif not response_text and not response_json:
        updates["status"] = "empty"
    else:
        updates["status"] = "ok"
        
    try:
        conn = get_db_connection()
        started_at_str = conn.execute("SELECT started_at FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
        if started_at_str and started_at_str[0]:
            started_at = datetime.fromisoformat(started_at_str[0])
            duration = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
            updates["duration_ms"] = duration
        conn.close()
    except Exception as e:
        logger.warning(f"Could not calculate duration: {e}")
        
    _safe_update(call_id, updates)


def mark_sanitized(cleaned: str):
    try:
        conn = get_db_connection()
        t_id = _trace_id.get()
        if not t_id:
            conn.close()
            return
        
        row = conn.execute("SELECT id FROM llm_calls WHERE trace_id = ? ORDER BY seq_in_trace DESC LIMIT 1", (t_id,)).fetchone()
        if not row:
            conn.close()
            return
            
        call_id = row[0]
        status = "sanitized_empty" if not cleaned.strip() else "ok"
        conn.execute("UPDATE llm_calls SET sanitized_text = ?, status = ? WHERE id = ?", (cleaned, status, call_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to mark_sanitized: {e}")


def link_outcome(outcome_type: str, outcome_ref: Any):
    try:
        conn = get_db_connection()
        t_id = _trace_id.get()
        if not t_id:
            conn.close()
            return
            
        conn.execute("UPDATE llm_calls SET outcome_type = ?, outcome_ref = ? WHERE trace_id = ?", 
                     (outcome_type, str(outcome_ref), t_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to link_outcome: {e}")


def adk_before_model(llm_request: Any, callback_context: Any) -> None:
    try:
        agent_name = getattr(callback_context, "agent_name", "unknown")
        
        sys_prompt = ""
        config = getattr(llm_request, "config", None)
        if config and hasattr(config, "system_instruction"):
            sys_prompt = config.system_instruction

        contents = getattr(llm_request, "contents", [])
        messages_json = json.dumps([c for c in contents if isinstance(c, dict)], ensure_ascii=False) if contents else "[]"
        
        state_dict = {}
        if hasattr(callback_context, "state") and hasattr(callback_context.state, "to_dict"):
            state_dict = callback_context.state.to_dict()
        
        tools_dict = getattr(llm_request, "tools_dict", None)
        tools_json = json.dumps(tools_dict, ensure_ascii=False) if tools_dict else None
        
        model = os.environ.get("ADK_MODEL", "openai/gpt-5.4")
        
        parent_id = state_dict.get("_llm_trace_last_call_id")
        
        call_id = start_call(
            agent_name=agent_name,
            model=model,
            system_prompt=sys_prompt,
            messages_json=messages_json,
            state_json=json.dumps(state_dict, ensure_ascii=False),
            tools_json=tools_json,
            parent_call_id=parent_id
        )
        
        if hasattr(callback_context, "state"):
            callback_context.state["_llm_trace_call_id"] = call_id
            callback_context.state["_llm_trace_last_call_id"] = call_id
    except Exception as e:
        logger.warning(f"adk_before_model failed: {e}\n{traceback.format_exc()}")


def adk_after_model(llm_response: Any, callback_context: Any) -> None:
    try:
        call_id = None
        if hasattr(callback_context, "state"):
            call_id = callback_context.state.get("_llm_trace_call_id")
            
        if not call_id:
            return
            
        response_text = ""
        if hasattr(llm_response, "content") and hasattr(llm_response.content, "parts"):
            parts = llm_response.content.parts
            response_text = "".join([getattr(p, "text", "") for p in parts])
            
        usage = getattr(llm_response, "usage_metadata", None)
        tokens_in = getattr(usage, "prompt_token_count", None) if usage else None
        tokens_out = getattr(usage, "candidates_token_count", None) if usage else None
        
        function_calls = getattr(llm_response, "function_calls", None)
        response_json = json.dumps(function_calls, ensure_ascii=False) if function_calls else None
        
        error = getattr(llm_response, "error", None)
        
        end_call(
            call_id=call_id,
            response_text=response_text,
            response_json=response_json,
            error=str(error) if error else None,
            tokens_in=tokens_in,
            tokens_out=tokens_out
        )
    except Exception as e:
        logger.warning(f"adk_after_model failed: {e}\n{traceback.format_exc()}")


def traced(agent: LlmAgent) -> LlmAgent:
    agent.before_model_callback = adk_before_model
    agent.after_model_callback = adk_after_model
    return agent
