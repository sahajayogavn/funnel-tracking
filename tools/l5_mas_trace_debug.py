#!/usr/bin/env python3
"""Read-only diagnostic CLI for the inbox MAS: one command, full context.

# code:mas-debug-001
Built for the "MAS gave a bad reply, find out why" workflow. It never calls an
LLM and never writes to the queue/DB — it only reads `llm_calls`,
`mas_decisions`, `action_queue` and `messages`, and re-runs the pure
`compute_conversation_state` gate so an agent can see what the gate *should*
have decided versus what actually happened.

See docs/architect/mas-debugging-playbook.md for the workflow this supports.

Usage:
    tools/l5_mas_trace_debug.py report <trace_id_or_thread_id>
    tools/l5_mas_trace_debug.py timeline <trace_id>
    tools/l5_mas_trace_debug.py call <call_id> [--full]
    tools/l5_mas_trace_debug.py thread <thread_id>
    tools/l5_mas_trace_debug.py find --thread <thread_id>
    tools/l5_mas_trace_debug.py find --subject <name>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection  # noqa: E402
from fb_pipeline.contracts.l1_conversation_state import (  # noqa: E402
    compute_conversation_state, format_conversation_lines, format_now_context,
)

TEXT_FIELDS = (
    "system_prompt", "messages_json", "state_json", "tools_json",
    "response_text", "response_json", "sanitized_text",
)


# code:mas-debug-001:calls
def get_trace_calls(trace_id: str) -> list[dict]:
    """Every `llm_calls` row for a trace_id, in actual execution order.

    Ordered by `id` (insertion order), not `seq_in_trace` — two independent
    runs have been observed to collide on the same trace_id (see
    docs/report/mas-execution-audit-2026-09-17.md follow-up), which makes
    `seq_in_trace` alone ambiguous.
    """
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM llm_calls WHERE trace_id = ? ORDER BY id", (trace_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# code:mas-debug-001:calls
def get_call(call_id: int) -> dict | None:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# code:mas-debug-001:lookup
def find_trace_ids_for_thread(thread_id: str, limit: int = 10) -> list[str]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT trace_id, MIN(started_at) AS first_seen FROM llm_calls "
            "WHERE subject_id = ? GROUP BY trace_id ORDER BY first_seen DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        return [r["trace_id"] for r in rows]
    finally:
        conn.close()


# code:mas-debug-001:lookup
def find_trace_ids_for_subject_label(name: str, limit: int = 10) -> list[str]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT trace_id, MIN(started_at) AS first_seen FROM llm_calls "
            "WHERE subject_label LIKE ? GROUP BY trace_id ORDER BY first_seen DESC LIMIT ?",
            (f"%{name}%", limit),
        ).fetchall()
        return [r["trace_id"] for r in rows]
    finally:
        conn.close()


# code:mas-debug-001:lookup
def resolve_thread_id_for_trace(trace_id: str) -> str | None:
    calls = get_trace_calls(trace_id)
    for c in calls:
        if c.get("subject_type") == "thread" and c.get("subject_id"):
            return c["subject_id"]
    return None


# code:mas-debug-001:thread
def get_thread_messages_raw(thread_id: str, limit: int = 200) -> list[dict]:
    """All genuine + banner/reaction rows (kind included) — deliberately
    unfiltered so a debugger can see what the fetcher actually stored, not
    only what an agent prompt was allowed to see."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT sender, content, message_timestamp, message_at, kind, seq "
            "FROM messages WHERE thread_id = ? ORDER BY seq ASC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# code:mas-debug-001:gate
def get_conversation_state(thread_id: str) -> dict:
    """Re-run the pure gate now, against genuine messages only.

    This answers "what SHOULD the deterministic gate have said" — compare it
    against the `inbox_gate` row in `mas_decisions` to see whether the gate
    was even consulted (manual_recommendation bypasses it by design; see
    docs/architect/mas-response-quality-runbook.md).
    """
    genuine = [m for m in get_thread_messages_raw(thread_id) if m.get("kind") == "message"]
    state = compute_conversation_state(genuine)
    return state.to_dict()


# code:mas-debug-001:decisions
def get_mas_decisions(thread_id: str, limit: int = 20) -> list[dict]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM mas_decisions WHERE subject_id = ? ORDER BY id DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# code:mas-debug-001:queue
def get_action_queue_items(thread_id: str, limit: int = 10) -> list[dict]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM action_queue WHERE target_id = ? ORDER BY id DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# code:mas-debug-001:report
def build_report(identifier: str) -> dict:
    """One call, full context: resolve trace_id or thread_id, then gather
    everything a human or an agent needs to root-cause a bad MAS reply."""
    calls = get_trace_calls(identifier)
    if calls:
        trace_id = identifier
        thread_id = resolve_thread_id_for_trace(trace_id)
    else:
        trace_id = None
        thread_id = identifier
        trace_ids = find_trace_ids_for_thread(thread_id)
        if trace_ids:
            trace_id = trace_ids[0]
            calls = get_trace_calls(trace_id)

    report = {
        "identifier": identifier,
        "resolved_trace_id": trace_id,
        "resolved_thread_id": thread_id,
        "now_context": format_now_context(),
    }
    if thread_id:
        raw_messages = get_thread_messages_raw(thread_id)
        report["conversation_state_now"] = get_conversation_state(thread_id)
        report["conversation_text"] = format_conversation_lines(
            [m for m in raw_messages if m.get("kind") == "message"]
        )
        report["mas_decisions"] = get_mas_decisions(thread_id)
        report["action_queue"] = get_action_queue_items(thread_id)
        report["other_trace_ids_for_thread"] = find_trace_ids_for_thread(thread_id)
    report["calls"] = calls
    return report


# ---- rendering -------------------------------------------------------

def _truncate(value, n=1200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text or ""
    return text if len(text) <= n else text[:n] + f"... [+{len(text) - n} chars]"


def render_timeline(calls: list[dict]) -> str:
    if not calls:
        return "(no llm_calls rows for this trace_id)"
    lines = [f"{'id':>5}  {'seq':>3}  {'agent':<20}  {'status':<6}  {'tok_in':>7}  {'tok_out':>7}  {'ms':>6}  started_at"]
    for c in calls:
        lines.append(
            f"{c['id']:>5}  {c.get('seq_in_trace', '?'):>3}  {str(c.get('agent_name'))[:20]:<20}  "
            f"{str(c.get('status'))[:6]:<6}  {c.get('tokens_in') or 0:>7}  {c.get('tokens_out') or 0:>7}  "
            f"{c.get('duration_ms') or 0:>6}  {c.get('started_at')}"
        )
    return "\n".join(lines)


def render_call_io(call: dict, full: bool = False) -> str:
    n = 100000 if full else 1200
    parts = [f"=== call #{call['id']} — {call.get('agent_name')} (trace {call.get('trace_id')}, seq {call.get('seq_in_trace')}) ==="]
    parts.append(f"status={call.get('status')} tokens_in={call.get('tokens_in')} tokens_out={call.get('tokens_out')} duration_ms={call.get('duration_ms')}")
    if call.get("error"):
        parts.append(f"error: {call['error']}")
    for field in TEXT_FIELDS:
        value = call.get(field)
        if not value:
            continue
        parts.append(f"--- {field} ---\n{_truncate(value, n)}")
    return "\n".join(parts)


def render_report(report: dict, full_calls: bool = False) -> str:
    lines = [
        f"Identifier: {report['identifier']}",
        f"Resolved trace_id: {report['resolved_trace_id']}",
        f"Resolved thread_id: {report['resolved_thread_id']}",
        report["now_context"],
    ]
    if report.get("other_trace_ids_for_thread"):
        lines.append(f"Other trace_ids seen for this thread (most recent first): {report['other_trace_ids_for_thread']}")
    if "conversation_state_now" in report:
        lines.append("\n## Deterministic gate, re-run now (compute_conversation_state)")
        lines.append(json.dumps(report["conversation_state_now"], ensure_ascii=False, indent=2))
    if "conversation_text" in report:
        lines.append("\n## Genuine conversation (kind='message')")
        lines.append(report["conversation_text"])
    if report.get("mas_decisions"):
        lines.append("\n## mas_decisions (most recent first)")
        for d in report["mas_decisions"]:
            lines.append(f"- [{d['created_at']}] route={d['route']} decision={d['decision']} reason={d.get('reason')}")
    if report.get("action_queue"):
        lines.append("\n## action_queue (most recent first)")
        for a in report["action_queue"]:
            lines.append(f"- #{a['id']} {a['queue_type']} status={a['status']} created_at={a['created_at']} text={_truncate(a.get('action_text'), 200)!r}")
    lines.append("\n## LLM call timeline")
    lines.append(render_timeline(report["calls"]))
    lines.append("\n## LLM call I/O")
    for c in report["calls"]:
        lines.append(render_call_io(c, full=full_calls))
    return "\n".join(lines)


# ---- CLI ---------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_report = sub.add_parser("report", help="Full context for a trace_id or thread_id, one shot.")
    p_report.add_argument("identifier")
    p_report.add_argument("--json", action="store_true")
    p_report.add_argument("--full", action="store_true", help="Do not truncate prompt/response text.")

    p_timeline = sub.add_parser("timeline", help="Compact step-by-step timeline for a trace_id.")
    p_timeline.add_argument("trace_id")

    p_call = sub.add_parser("call", help="Full input/output for one llm_calls.id.")
    p_call.add_argument("call_id", type=int)
    p_call.add_argument("--full", action="store_true")

    p_thread = sub.add_parser("thread", help="Gate state + decisions + queue + messages for a thread_id.")
    p_thread.add_argument("thread_id")
    p_thread.add_argument("--json", action="store_true")

    p_find = sub.add_parser("find", help="List recent trace_ids for a thread or a subject name.")
    p_find.add_argument("--thread")
    p_find.add_argument("--subject")

    args = parser.parse_args()

    if args.cmd == "report":
        report = build_report(args.identifier)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_report(report, full_calls=args.full))
    elif args.cmd == "timeline":
        print(render_timeline(get_trace_calls(args.trace_id)))
    elif args.cmd == "call":
        call = get_call(args.call_id)
        print(render_call_io(call, full=args.full) if call else f"No llm_calls row with id={args.call_id}")
    elif args.cmd == "thread":
        data = {
            "conversation_state_now": get_conversation_state(args.thread_id),
            "mas_decisions": get_mas_decisions(args.thread_id),
            "action_queue": get_action_queue_items(args.thread_id),
            "messages": get_thread_messages_raw(args.thread_id),
        }
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(data["conversation_state_now"], ensure_ascii=False, indent=2))
            print("\n## mas_decisions")
            for d in data["mas_decisions"]:
                print(f"- [{d['created_at']}] route={d['route']} decision={d['decision']} reason={d.get('reason')}")
            print("\n## action_queue")
            for a in data["action_queue"]:
                print(f"- #{a['id']} {a['queue_type']} status={a['status']} created_at={a['created_at']}")
    elif args.cmd == "find":
        if args.thread:
            print("\n".join(find_trace_ids_for_thread(args.thread)))
        elif args.subject:
            print("\n".join(find_trace_ids_for_subject_label(args.subject)))
        else:
            parser.error("find requires --thread or --subject")


if __name__ == "__main__":
    main()
