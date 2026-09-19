"""
Tools exposed to the InboxOrchestrator agent.
code:agent-mas-002:orchestrator-tools

These are the two side-effecting tools the orchestrator loop may call while it
is still working on one reply: re-reading the seeker's CRM profile, and
proposing/applying a correction to a strategic field (city, program_code,
thread_name) when the conversation reveals the classifier's first guess was
wrong or stale. Every other specialist (ConversationAnalyst, KnowledgeLibrarian,
ReplyComposer, ReplyQAReviewer) is wired in as an AgentTool in adk_agents/agent.py;
these two are plain FunctionTools with no LLM call of their own.
"""
import logging
import os
import sys
import json

logger = logging.getLogger("mas.orchestrator_tools")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from google.adk.tools import ToolContext

from fb_pipeline.persistence.l4_sqlite_store import get_user_row, record_seeker_field_change
from tools.l5_inbox_mas_context import build_knowledge_context

# code:agent-mas-002:auto-apply-threshold — a MAS-proposed correction below this
# confidence is recorded but never written to `users`; it only reaches the
# seeker record once a human approves it via the escalation/HITL flow.
AUTO_APPLY_CONFIDENCE = 0.75


def get_seeker_profile(thread_id: str) -> dict:
    """Read the seeker's current CRM profile, including who last set each
    strategic field (city_source/program_code_source: 'classifier', 'mas', or
    'human'). Call this again after propose_seeker_update to confirm what was
    actually written, since a human-owned field is never auto-overwritten.

    Args:
        thread_id: The thread ID from Facebook inbox (format: pageId_hash).

    Returns:
        dict: name, phone, city, city_source, program_code, program_code_source,
              lead_stage, or a 'not_found' status if no record exists.
    """
    try:
        row = get_user_row(thread_id)
        if not row:
            return {"status": "not_found", "thread_id": thread_id}
        return {
            "status": "found",
            "name": row.get("thread_name"),
            "phone": row.get("phone"),
            "city": row.get("city"),
            "city_source": row.get("city_source") or "classifier",
            "program_code": row.get("program_code"),
            "program_code_source": row.get("program_code_source") or "classifier",
            "lead_stage": row.get("lead_stage") or "Intake",
        }
    except Exception as exc:
        logger.error("get_seeker_profile failed: %s", exc)
        return {"status": "error", "error": str(exc)}


def propose_seeker_update(thread_id: str, field: str, new_value: str, evidence_seq: int,
                          confidence: float, reason: str, tool_context: ToolContext) -> dict:
    """Propose a correction to one seeker's city, program_code, or thread_name
    because the conversation shows the current value is wrong or the seeker
    moved/switched programs. ALWAYS cite the exact message that is your
    evidence via evidence_seq.

    High-confidence proposals (>= 0.75) are applied immediately so the rest of
    this loop (KnowledgeLibrarian, ReplyComposer) sees the corrected value —
    UNLESS a human operator already set that field, in which case only another
    human correction may change it again. Lower-confidence proposals are only
    recorded for a human to review; they never overwrite the live record.

    Args:
        thread_id: The thread ID from Facebook inbox.
        field: One of "city", "program_code", "thread_name".
        new_value: The corrected value.
        evidence_seq: The message `seq` in this conversation that justifies the change.
        confidence: 0.0-1.0 how sure you are, based only on what the seeker actually said.
        reason: One short sentence: what the seeker said that justifies this.

    Returns:
        dict: status ('applied', 'proposed', 'blocked_human_owned', or 'error'),
              plus old_value/new_value for confirmation.
    """
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    apply = confidence >= AUTO_APPLY_CONFIDENCE
    result = record_seeker_field_change(
        thread_id, field, new_value,
        evidence_seq=evidence_seq, reason=reason, source="mas",
        confidence=confidence, apply=apply,
    )
    # The profile is also present in the current ADK session as JSON.  Keep it
    # coherent with the applied database value so every later specialist sees
    # the correction in this same invocation; otherwise Composer could still
    # mention the old city even after retrieval has been refreshed.
    if result.get("status") == "applied":
        try:
            profile = json.loads(tool_context.state.get("seeker_context") or "{}")
            profile[field] = result["new_value"]
            if field in {"city", "program_code"}:
                profile[f"{field}_source"] = "mas"
            tool_context.state["seeker_context"] = json.dumps(profile, ensure_ascii=False, indent=2)
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("Applied seeker update but could not refresh session profile: %s", exc)
    logger.info("propose_seeker_update thread=%s field=%s -> %s (%s)",
                thread_id, field, new_value, result.get("status"))
    return result


def get_knowledge(city: str, question: str, tool_context: ToolContext) -> dict:
    """Retrieve grounding knowledge (classes, FAQ, events, contacts) for this
    seeker and question, and place it in the current session for the Librarian
    and Composer. This is the only path that populates ``knowledge_context``;
    do not rely on a city snapshot assembled before the MAS invocation.

    Args:
        city: The seeker's currently resolved city. Use ``Unknown`` if it is
            genuinely unresolved, so the response can ask the seeker instead
            of borrowing a different city's schedule.
        question: The seeker's latest question, used to select relevant FAQ entries.
        tool_context: Injected automatically; do not pass this yourself.

    Returns:
        dict: status and a short confirmation. The retrieved knowledge is written
              directly into session state for KnowledgeLibrarian/ReplyComposer to use.
    """
    try:
        knowledge_context = build_knowledge_context([city], question or "", include_soul=True)
        tool_context.state["knowledge_context"] = knowledge_context
        return {"status": "refreshed", "city": city, "chars": len(knowledge_context)}
    except Exception as exc:
        logger.error("refresh_knowledge_context failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# Compatibility alias for any in-flight invocation serialized before the
# retrieval tool was renamed. New agent wiring uses get_knowledge.
refresh_knowledge_context = get_knowledge
