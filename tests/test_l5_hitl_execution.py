"""Isolation contract for the standalone human-approved action executor."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hitl_executor_is_not_registered_by_mas_scheduler():
    scheduler_source = (ROOT / "tools" / "l5_scheduler.py").read_text(encoding="utf-8")
    assert "hitl_execution_job" not in scheduler_source
    assert "telegram_poller_job" not in scheduler_source


def test_standalone_worker_only_imports_delivery_dependencies():
    source = (ROOT / "tools" / "l5_hitl_execution.py").read_text(encoding="utf-8")
    assert "def hitl_execution_job" in source
    assert "def run_hitl_loop" in source
    assert "run_fetch_cycle" not in source
    assert "run_adk_pipeline" not in source
    assert "run_adk_warmup_composer" not in source


def test_delivery_requires_claimed_human_approval():
    from tools.l5_hitl_execution import OUTBOUND_QUEUE_TYPES, _execute_approved_action

    assert "proactive_message" in OUTBOUND_QUEUE_TYPES
    try:
        _execute_approved_action({"id": 1, "queue_type": "reply_message"}, "page", dry_run=False)
    except RuntimeError as exc:
        assert "human-approved" in str(exc).lower()
    else:
        raise AssertionError("Unclaimed actions must never deliver")


def test_shell_runner_requires_an_explicit_mode():
    source = (ROOT / "tools" / "run_hitl_execution_loop.sh").read_text(encoding="utf-8")
    assert "{live|dry-run}" in source
    assert "l5_hitl_execution.py" in source
