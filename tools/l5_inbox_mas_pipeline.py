"""Isolated, traceable inbox MAS execution: one eligible message per run."""
import asyncio
import json
import logging
import re

from fb_pipeline.persistence.l4_llm_trace import get_trace_context, get_trigger, mark_sanitized, span
from tools.l5_adk_runtime import run_runner

logger = logging.getLogger("inbox_mas_pipeline")


def run_adk_pipeline(thread_messages: list, seeker_context: dict, feedback: str = None, *,
                     trigger: str | None = None, page_id: str | None = None,
                     subject_id: str | None = None, trace_id: str | None = None,
                     now_context: str | None = None,
                     conversation_state: dict | None = None,
                     reaction_events: list[dict] | None = None) -> dict:
    """Run one message-scoped MAS action under one durable trace."""
    subject_id = subject_id or seeker_context.get("thread_id") or seeker_context.get("id") or seeker_context.get("name") or "unknown"
    # code:agent-mas-002:orchestrator-thread-id — the InboxOrchestrator's tools
    # (get_seeker_profile, propose_seeker_update) take thread_id as an explicit
    # argument, so it must always be present in the seeker_context the model sees.
    seeker_context = {**seeker_context, "thread_id": seeker_context.get("thread_id") or str(subject_id)}
    with span(trigger=trigger or (get_trigger() if get_trigger() != "unknown" else "cli"), route="propose",
              page_id=page_id, subject=("thread", str(subject_id), seeker_context.get("name") or str(subject_id)),
              dry_run=True, trace_id=trace_id):
        return _run_adk_pipeline(
            thread_messages, seeker_context, feedback, now_context=now_context,
            conversation_state=conversation_state, reaction_events=reaction_events,
        )


# code:agent-mas-003:care-runtime
def run_adk_care_pipeline(thread_messages: list, seeker_context: dict, *,
                          care_purpose: str, care_brief: dict,
                          feedback: str | None = None, trigger: str | None = None,
                          page_id: str | None = None, subject_id: str | None = None,
                          trace_id: str | None = None, now_context: str | None = None,
                          reaction_events: list[dict] | None = None) -> dict:
    """Run one proactive care action through CareOrchestrator.

    Eligibility is intentionally established by the caller before this LLM
    session. This function supplies the verified facts and preserves the same
    trace/result contract as ``run_adk_pipeline``.
    """
    if care_purpose not in {"class_reminder", "warmup", "event"}:
        raise ValueError(f"Unsupported care purpose: {care_purpose}")
    subject_id = subject_id or seeker_context.get("thread_id") or seeker_context.get("id") or seeker_context.get("name") or "unknown"
    seeker_context = {**seeker_context, "thread_id": seeker_context.get("thread_id") or str(subject_id)}
    with span(trigger=trigger or (get_trigger() if get_trigger() != "unknown" else "cli"), route="care",
              page_id=page_id, subject=("thread", str(subject_id), seeker_context.get("name") or str(subject_id)),
              dry_run=True, trace_id=trace_id):
        return _run_adk_care_pipeline(
            thread_messages, seeker_context, care_purpose, care_brief, feedback, now_context=now_context,
            reaction_events=reaction_events,
        )


def _run_adk_pipeline(thread_messages: list, seeker_context: dict, feedback: str | None = None,
                      now_context: str | None = None,
                      conversation_state: dict | None = None,
                      reaction_events: list[dict] | None = None) -> dict:
    from adk_agents.agent import root_agent
    from fb_pipeline.contracts.l1_conversation_state import format_conversation_lines, format_now_context
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    conversation_text = format_conversation_lines(thread_messages, reaction_events)
    now_context = now_context or format_now_context()
    seeker_text = json.dumps(seeker_context, ensure_ascii=False, indent=2)
    # Knowledge is deliberately not assembled from the inbound city here. The
    # Librarian calls get_knowledge in its own tool turn after the orchestrator
    # has inspected the message/profile, which prevents a misclassified city
    # from silently anchoring the rest of the run to the wrong schedule.
    knowledge_context = ""
    state = {"_llm_trace_context": get_trace_context(), "_llm_trace_seq": 0, "now_context": now_context,
             "thread_messages": conversation_text, "seeker_context": seeker_text, "knowledge_context": knowledge_context,
             "conversation_state": json.dumps(conversation_state or {}, ensure_ascii=False),
             "message_scope": "one eligible thread / latest customer message"}
    service = InMemorySessionService()
    session = asyncio.run(service.create_session(app_name="sahajayoga_inbox", user_id="inbox_runner", state=state))
    runner = Runner(agent=root_agent, app_name="sahajayoga_inbox", session_service=service)
    prompt = "Process this one eligible Facebook inbox message using the session state."
    if feedback:
        prompt += f"\nHuman feedback: {feedback}"
    message = types.Content(role="user", parts=[types.Part(text=prompt)])
    result = {"classification": "", "draft_reply": "", "qa_verdict": "", "reply_text": "",
              "thread_messages": conversation_text, "seeker_context": seeker_text, "knowledge_context": knowledge_context,
              "escalation_reason": "", "escalation_note": "", "loop_count": 0}
    final_text = ""
    for event in run_runner(runner, user_id="inbox_runner", session_id=session.id, new_message=message):
        if not getattr(event, "content", None) or not event.content.parts:
            continue
        text, author = event.content.parts[0].text or "", getattr(event, "author", "")
        if author == "InboxOrchestrator" and text.strip():
            final_text = text
    # The 4 specialists ran as tool calls inside the orchestrator's own session;
    # their output_key writes reach this same session's state via ADK's tool
    # state_delta forwarding (code:agent-mas-002:orchestrator). Read the final
    # state once, after the run, for the audit-friendly breakdown fields.
    final_session = asyncio.run(service.get_session(app_name="sahajayoga_inbox", user_id="inbox_runner", session_id=session.id))
    final_state = final_session.state if final_session else {}
    result["classification"] = final_state.get("conversation_analysis", "")
    result["draft_reply"] = final_state.get("draft_reply", "")
    result["qa_verdict"] = final_state.get("qa_verdict", "")
    result["knowledge_context"] = final_state.get("knowledge_context", knowledge_context)
    result["loop_count"] = int(final_state.get("_orchestrator_loop_count") or 0)
    result["reply_text"] = final_text

    escalation_reason, escalation_note = _parse_escalation(result["reply_text"])
    if escalation_reason:
        # Specialist outputs are preserved in session state. If QA calls them
        # absent while both the analysis and factual brief are present, it is a
        # reviewer false positive, not a knowledge gap. Keep the grounded
        # draft for the ordinary deterministic safety check below.
        if _is_spurious_knowledge_gap(escalation_reason, result):
            logger.warning("Ignoring QA knowledge_gap: grounded analysis and brief are present")
            result["qa_verdict"] = f"OVERRIDDEN_SPURIOUS_KNOWLEDGE_GAP: {result['qa_verdict']}"
            result["reply_text"] = _sanitize_reply(result["draft_reply"])
        else:
            result["escalation_reason"] = escalation_reason
            result["escalation_note"] = escalation_note
            result["reply_text"] = _sanitize_reply(escalation_note or "")
            return result

    # The orchestrator is untrusted control flow: an ordinary final turn must
    # never turn a missing/REPAIR QA into an outbound proposal.  The only
    # sendable text is the exact draft that QA approved, not a later model turn.
    approved = _approved_draft(result)
    if approved is None:
        logger.warning("Inbox MAS result rejected: QA did not PASS the exact draft")
        result["reply_text"] = ""
    else:
        result["reply_text"] = approved
    return result


def _run_adk_care_pipeline(thread_messages: list, seeker_context: dict, care_purpose: str,
                           care_brief: dict, feedback: str | None = None,
                           now_context: str | None = None,
                           reaction_events: list[dict] | None = None) -> dict:
    """Run a fixed, auditable Care workflow without an LLM orchestration loop.

    The specialist agents still own language understanding, retrieval, drafting
    and review. Python owns the order, composer selection, bounded repair loop,
    and the PASS/version invariant. This removes the six orchestration turns
    observed in trace review and makes a prompt-only contract enforceable.
    """
    from adk_agents.agent import (
        class_reminder_composer, conversation_analyst, event_advertiser,
        knowledge_librarian, reply_qa_reviewer, warmup_composer,
    )
    from fb_pipeline.contracts.l1_conversation_state import format_conversation_lines, format_now_context
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    conversation_text = format_conversation_lines(thread_messages, reaction_events)
    now_context = now_context or format_now_context()
    seeker_text = json.dumps(seeker_context, ensure_ascii=False, indent=2)
    brief_text = json.dumps(care_brief, ensure_ascii=False, indent=2)
    reminder_brief = json.dumps({
        "seeker": seeker_context,
        "session": care_brief.get("verified_session"),
        "prior_page_lines": care_brief.get("prior_page_lines") or [],
        "operator_instruction": care_brief.get("operator_instruction") or feedback or "",
        "recent_conversation": care_brief.get("recent_conversation") or "",
    }, ensure_ascii=False, indent=2)
    warmup_brief = json.dumps({
        "seeker_context": seeker_context,
        "strategy": care_brief.get("warmup_strategy"),
        "knowledge_context": care_brief.get("knowledge_context") or "",
        "operator_instruction": care_brief.get("operator_instruction") or feedback or "",
        "recent_conversation": care_brief.get("recent_conversation") or "",
    }, ensure_ascii=False, indent=2)
    event_details = json.dumps({
        **(care_brief.get("verified_event") or {}),
        "knowledge_context": care_brief.get("knowledge_context") or "",
        "operator_instruction": care_brief.get("operator_instruction") or feedback or "",
    }, ensure_ascii=False, indent=2)
    state = {
        "_llm_trace_context": get_trace_context(), "_llm_trace_seq": 0,
        "now_context": now_context, "thread_messages": conversation_text,
        "seeker_context": seeker_text, "knowledge_context": "",
        "care_purpose": care_purpose, "care_brief": brief_text,
        "conversation_state": json.dumps(care_brief.get("conversation_state") or {}, ensure_ascii=False),
        # Each specialist retains its established input field, while all share
        # the same analytical and QA state in this one orchestrated session.
        "reminder_brief": reminder_brief if care_purpose == "class_reminder" else "",
        "warmup_brief": warmup_brief if care_purpose == "warmup" else "",
        "event_details": event_details if care_purpose == "event" else "",
        "message_scope": "one selected proactive care action",
    }
    composer_by_purpose = {
        "class_reminder": class_reminder_composer,
        "warmup": warmup_composer,
        "event": event_advertiser,
    }
    composer = composer_by_purpose[care_purpose]
    service = InMemorySessionService()
    session = asyncio.run(service.create_session(app_name="sahajayoga_care", user_id="care_runner", state=state))
    result = {"classification": "", "draft_reply": "", "qa_verdict": "", "reply_text": "",
              "thread_messages": conversation_text, "seeker_context": seeker_text, "knowledge_context": "",
              "escalation_reason": "", "escalation_note": "", "loop_count": 0}

    def run_specialist(agent, prompt: str) -> None:
        runner = Runner(agent=agent, app_name="sahajayoga_care", session_service=service)
        message = types.Content(role="user", parts=[types.Part(text=prompt)])
        # State output keys, not free-form event text, are the handoff API.
        for _event in run_runner(runner, user_id="care_runner", session_id=session.id, new_message=message):
            pass

    try:
        run_specialist(conversation_analyst, "Analyze this selected care action using the session state. Do not draft.")
        run_specialist(knowledge_librarian, "Ground this selected care action using the session state. Do not draft.")
        repair_feedback = ""
        for repair_attempt in range(3):
            composer_prompt = f"Draft this {care_purpose} action using the verified handoffs in session state."
            if repair_feedback:
                # get_session() intentionally returns a copy in current ADK;
                # pass repair feedback in the next model request rather than
                # mutating that copy and hoping it persists.
                composer_prompt += f"\nMandatory QA correction for this rewrite: {repair_feedback}"
            run_specialist(composer, composer_prompt)
            draft_session = asyncio.run(service.get_session(
                app_name="sahajayoga_care", user_id="care_runner", session_id=session.id,
            ))
            draft_state = draft_session.state if draft_session else {}
            qa_prompt = _care_qa_prompt(
                care_purpose=care_purpose,
                now_context=now_context,
                conversation_text=conversation_text,
                conversation_state=care_brief.get("conversation_state") or {},
                seeker_context=seeker_context,
                care_brief=care_brief,
                analysis=draft_state.get("conversation_analysis", ""),
                knowledge=draft_state.get("knowledge_brief", ""),
                draft=draft_state.get("draft_reply", ""),
            )
            run_specialist(reply_qa_reviewer, qa_prompt)
            final_session = asyncio.run(service.get_session(
                app_name="sahajayoga_care", user_id="care_runner", session_id=session.id,
            ))
            final_state = final_session.state if final_session else {}
            verdict = str(final_state.get("qa_verdict") or "").strip()
            result["loop_count"] += 2
            if verdict.upper() == "PASS":
                break
            if verdict.upper().startswith("ESCALATE:"):
                reason, _, note = verdict.partition(":")
                reason_code, _, detail = note.strip().partition(":")
                result["escalation_reason"] = reason_code.strip().lower() or "policy_uncertain"
                result["escalation_note"] = detail.strip()
                return _populate_care_result(result, final_state)
            if not verdict.upper().startswith("REPAIR:") or repair_attempt == 2:
                result["escalation_reason"] = "invalid_result"
                result["escalation_note"] = "QA did not return PASS for a sendable draft."
                return _populate_care_result(result, final_state)
            repair_feedback = verdict.partition(":")[2].strip()
    except Exception as exc:
        logger.exception("Care specialist workflow failed")
        result["escalation_reason"] = "invalid_result"
        result["escalation_note"] = f"Care workflow failed: {exc}"
        return result

    final_session = asyncio.run(service.get_session(app_name="sahajayoga_care", user_id="care_runner", session_id=session.id))
    final_state = final_session.state if final_session else {}
    result = _populate_care_result(result, final_state)
    approved = _approved_draft(result)
    if approved is None:
        result["escalation_reason"] = "invalid_result"
        result["escalation_note"] = "QA did not PASS the exact draft."
        result["reply_text"] = ""
    else:
        result["reply_text"] = approved
    return result


def _populate_care_result(result: dict, state: dict) -> dict:
    """Copy durable specialist handoffs into the public pipeline result."""
    result["classification"] = state.get("conversation_analysis", "")
    result["draft_reply"] = state.get("draft_reply", "")
    result["qa_verdict"] = state.get("qa_verdict", "")
    result["knowledge_context"] = state.get("knowledge_brief", state.get("knowledge_context", ""))
    return result


def _care_qa_prompt(*, care_purpose: str, now_context: str, conversation_text: str,
                    conversation_state: dict, seeker_context: dict, care_brief: dict,
                    analysis: str, knowledge: str, draft: str) -> str:
    """Build a standalone QA snapshot; history is supplementary, never evidence.

    Keeping these values in the user message makes the QA input auditable at
    the ADK/model boundary and prevents a terse specialist handoff from
    silently removing the operator instruction or original conversation.
    """
    return (
        "Review this selected care draft against the complete source snapshot.\n"
        f"Purpose: {care_purpose}\n"
        f"Current time: {now_context}\n"
        f"Original transcript:\n{conversation_text}\n"
        f"Deterministic conversation state:\n{json.dumps(conversation_state, ensure_ascii=False)}\n"
        f"Seeker profile:\n{json.dumps(seeker_context, ensure_ascii=False)}\n"
        f"Operator care brief and verified session/event facts:\n{json.dumps(care_brief, ensure_ascii=False)}\n"
        f"Conversation analysis:\n{analysis}\n"
        f"Verified knowledge brief:\n{knowledge}\n"
        f"Draft under review:\n{draft}\n"
        "Return only PASS, REPAIR: <correction>, or ESCALATE: <reason_code>: <note>."
    )


def _approved_draft(result: dict) -> str | None:
    """Return the one approved draft, otherwise fail closed.

    QA approval is only valid for the exact sanitized draft in the same session.
    A later orchestrator final turn, REPAIR/ESCALATE/missing verdict, or unsafe
    text cannot be enqueued as a DM.
    """
    if str(result.get("qa_verdict") or "").strip().upper() != "PASS":
        return None
    draft = _sanitize_reply(str(result.get("draft_reply") or ""))
    final = _sanitize_reply(str(result.get("reply_text") or ""))
    if final and final != draft:
        return None
    if not _is_safe_final_reply(draft):
        return None
    return draft


# code:agent-mas-002:escalation-sentinel
_ESCALATE_PATTERN = re.compile(r"^\[ESCALATE:\s*([a-z_]+)\]\s*(.*)$", re.I | re.S)


def _parse_escalation(text: str) -> tuple[str, str]:
    """Parse the `[ESCALATE: <reason_code>] <note>` sentinel the orchestrator's
    final turn uses when a human, not another rewrite, must answer.

    Returns (reason_code, note), or ("", "") when text isn't an escalation.
    """
    if not text:
        return "", ""
    match = _ESCALATE_PATTERN.match(text.strip())
    if not match:
        return "", ""
    return match.group(1).strip().lower(), match.group(2).strip()


def _is_spurious_knowledge_gap(escalation_reason: str, result: dict) -> bool:
    """Whether QA contradicted the grounded state it was asked to review."""
    return (
        escalation_reason == "knowledge_gap"
        and bool(str(result.get("conversation_analysis") or "").strip())
        and bool(str(result.get("knowledge_context") or "").strip())
        and bool(str(result.get("draft_reply") or "").strip())
    )


def _is_safe_final_reply(text: str) -> bool:
    if not text or not text.strip():
        return False
    value = text.strip()
    if value.startswith("[NO_REPLY:") or value == "[OUT_OF_SCOPE]":
        return True
    return (len(value) <= 2000
            and not any(token in value.lower() for token in
                        ("**", "i need to", "let me", "here is the reply"))
            and not _has_forbidden_page_tone(value))


def _has_forbidden_page_tone(text: str) -> bool:
    """Block drafts that read like peer-to-peer shorthand rather than the Page."""
    lowered = text.strip().lower()
    has_single_letter_shorthand = bool(re.search(
        r"(?:^|[\s,.;:!?])(b|m)(?=$|[\s,.;:!?])|\bb\s*/\s*m\b", lowered
    ))
    has_casual_opener = bool(re.match(r"^(?:ừ|uh|ừm|ừh|ok|oke)(?:$|[\s,.;:!?])", lowered))
    return has_single_letter_shorthand or has_casual_opener


def _sanitize_reply(text: str) -> str:
    """Remove reasoning-leak lines before a reply can enter the HITL queue."""
    if not text:
        return text
    pattern = re.compile(r"^(\*\*.*\*\*|I need to\b|I'm (?:going to|working|thinking|attempting)\b|Let me\b|I should\b|I want to\b|I'll\b|Here is the reply|Here's (?:my|the) reply)", re.I)
    cleaned = "\n".join(line for line in text.splitlines() if not pattern.match(line.strip())).strip()
    mark_sanitized(cleaned)
    return cleaned
