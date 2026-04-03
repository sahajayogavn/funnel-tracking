import os
import sys
from datetime import datetime, timedelta

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TestCoolSequence:
    def test_get_next_cool_step_advances_until_exhausted(self):
        from tools.l5_scheduler_core import _get_next_cool_step

        assert _get_next_cool_step({"cool_step": 0}) == 1
        assert _get_next_cool_step({"cool_step": 1}) == 2
        assert _get_next_cool_step({"cool_step": 2}) == 3
        assert _get_next_cool_step({"cool_step": 3}) is None

    def test_cool_sequence_templates_match_strategy_playbook(self):
        from tools.l5_scheduler_core import COOL_SEQUENCE_TEMPLATES

        assert "lâu rồi" in COOL_SEQUENCE_TEMPLATES[1].lower()
        assert "5 phút" in COOL_SEQUENCE_TEMPLATES[2].lower()
        assert "{city}" in COOL_SEQUENCE_TEMPLATES[3]

    def test_evaluate_proactive_eligibility_blocks_dormant_warmup(self, monkeypatch):
        import tools.l5_scheduler_core as sched

        monkeypatch.setattr(sched, "_load_user_state", lambda thread_id: {
            "thread_id": thread_id,
            "lead_stage": "Seeker",
            "last_interaction": "2026-01-01T00:00:00",
            "temperature": "dormant",
        })
        monkeypatch.setattr(sched, "_thread_has_pending_reply", lambda page_id, thread_id: False)
        monkeypatch.setattr(sched, "_recent_live_touch_exists", lambda thread_id, since_hours=24: False)
        monkeypatch.setattr(sched, "_has_recent_live_event", lambda thread_id, since_days=90: False)

        eligible, reason, payload = sched._evaluate_proactive_eligibility("page-1", "warmup", "thread-1")

        assert eligible is False
        assert reason == "dormant_blocks_warmup"
        assert payload["temperature"] == "dormant"

    def test_run_warmup_cycle_blocks_step_two_before_three_day_gap(self, monkeypatch):
        import tools.l5_scheduler_core as sched_core
        import tools.l5_scheduler_routes as sched_routes
        import tools.l5_scheduler_adk as sched_adk

        decisions = []
        updates = []

        monkeypatch.setattr("tools.l5_fetch_fb_messages.fetch_messages", lambda *a, **k: {"success": True})

        monkeypatch.setattr(
            "adk_agents.tools.l5_warmup_tools.find_dormant_seekers",
            lambda page_id, max_seekers=5: {
                "status": "success",
                "count": 1,
                "seekers": [{
                    "thread_id": "thread-1",
                    "name": "Lan",
                    "city": "Hà Nội",
                    "lead_stage": "Seeker",
                    "days_dormant": 8,
                    "temperature": "cool",
                    "source": "inbox",
                }],
            },
        )
        monkeypatch.setattr("adk_agents.tools.l5_warmup_tools.was_recently_warmed_up", lambda *a, **k: False)
        monkeypatch.setattr("adk_agents.tools.l5_warmup_tools.select_warmup_strategy", lambda *a, **k: None)
        monkeypatch.setattr("adk_agents.tools.l5_warmup_tools.log_warmup_campaign", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not log campaign")))
        monkeypatch.setattr("tools.l5_inbox_mas_runner.load_knowledge_context", lambda: "KB")
        monkeypatch.setattr("tools.l5_scheduler_routes._load_user_state", lambda thread_id: {
            "thread_id": thread_id,
            "cool_step": 1,
            "last_warmup_at": (datetime.now() - timedelta(days=2)).isoformat(),
            "city": "Hà Nội",
        })
        monkeypatch.setattr("tools.l5_scheduler_routes._evaluate_proactive_eligibility", lambda *a, **k: (True, "eligible", {
            "thread_id": "thread-1",
            "temperature": "cool",
            "lead_stage": "Seeker",
            "last_interaction": (datetime.now() - timedelta(days=8)).isoformat(),
        }))
        monkeypatch.setattr("tools.l5_scheduler_routes.run_adk_warmup_composer", lambda *a, **k: "")
        monkeypatch.setattr("tools.l5_scheduler_routes._update_user_decision_state", lambda *a, **k: updates.append((a, k)))
        monkeypatch.setattr(
            "fb_pipeline.persistence.l4_sqlite_store.log_mas_decision",
            lambda page_id, route, subject_type, subject_id, decision, reason, dry_run=True, payload=None: decisions.append({
                "route": route,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "decision": decision,
                "reason": reason,
                "payload": payload,
            }),
        )

        result = sched_routes.run_warmup_cycle("page-1", dry_run=True, max_seekers=1)

        assert result["status"] == "complete"
        assert result["processed"] == 0
        assert result["skipped"] == 1
        assert decisions[0]["reason"] == "cool_step_interval_pending"
        assert decisions[0]["payload"]["required_gap_days"] == 3
        assert decisions[0]["payload"]["next_cool_step"] == 2
        assert updates == []

    def test_run_warmup_cycle_exhausts_after_step_three(self, monkeypatch):
        import tools.l5_scheduler_core as sched_core
        import tools.l5_scheduler_routes as sched_routes
        import tools.l5_scheduler_adk as sched_adk

        decisions = []
        updates = []

        monkeypatch.setattr("tools.l5_fetch_fb_messages.fetch_messages", lambda *a, **k: {"success": True})

        monkeypatch.setattr(
            "adk_agents.tools.l5_warmup_tools.find_dormant_seekers",
            lambda page_id, max_seekers=5: {
                "status": "success",
                "count": 1,
                "seekers": [{
                    "thread_id": "thread-9",
                    "name": "Minh",
                    "city": "Đà Nẵng",
                    "lead_stage": "Seeker",
                    "days_dormant": 20,
                    "temperature": "cool",
                    "source": "inbox",
                }],
            },
        )
        monkeypatch.setattr("adk_agents.tools.l5_warmup_tools.was_recently_warmed_up", lambda *a, **k: False)
        monkeypatch.setattr("adk_agents.tools.l5_warmup_tools.select_warmup_strategy", lambda *a, **k: None)
        monkeypatch.setattr("adk_agents.tools.l5_warmup_tools.log_warmup_campaign", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not log campaign")))
        monkeypatch.setattr("tools.l5_inbox_mas_runner.load_knowledge_context", lambda: "KB")
        monkeypatch.setattr("tools.l5_scheduler_routes._load_user_state", lambda thread_id: {
            "thread_id": thread_id,
            "cool_step": 3,
            "last_warmup_at": (datetime.now() - timedelta(days=9)).isoformat(),
            "city": "Đà Nẵng",
        })
        monkeypatch.setattr("tools.l5_scheduler_routes._evaluate_proactive_eligibility", lambda *a, **k: (True, "eligible", {
            "thread_id": "thread-9",
            "temperature": "cool",
            "lead_stage": "Seeker",
            "last_interaction": (datetime.now() - timedelta(days=20)).isoformat(),
        }))
        monkeypatch.setattr("tools.l5_scheduler_routes.run_adk_warmup_composer", lambda *a, **k: "")
        monkeypatch.setattr("tools.l5_scheduler_routes._update_user_decision_state", lambda *a, **k: updates.append((a, k)))
        monkeypatch.setattr(
            "fb_pipeline.persistence.l4_sqlite_store.log_mas_decision",
            lambda page_id, route, subject_type, subject_id, decision, reason, dry_run=True, payload=None: decisions.append({
                "decision": decision,
                "reason": reason,
                "payload": payload,
            }),
        )

        result = sched_routes.run_warmup_cycle("page-1", dry_run=True, max_seekers=1)

        assert result["processed"] == 0
        assert result["skipped"] == 1
        assert decisions[0]["reason"] == "cool_sequence_exhausted"
        assert decisions[0]["payload"]["temperature"] == "cold"
        assert decisions[0]["payload"]["cool_step"] == 0
        assert updates[0][0] == ("thread-9", "cold")
        assert updates[0][1]["warmup_sent"] is False
        assert updates[0][1]["cool_step"] == 0
