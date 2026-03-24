import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TestStageGates:
    def test_gate_g1_promotes_intake_after_touchpoint(self, monkeypatch):
        import adk_agents.tools.l5_stage_tools as stage_tools

        monkeypatch.setattr(stage_tools, "_get_user_and_thread", lambda thread_id: {
            "thread_id": thread_id,
            "thread_name": "Lan",
            "page_id": "page-1",
            "lead_stage": "Intake",
        })
        monkeypatch.setattr(stage_tools, "_get_thread_messages", lambda thread_id: [])
        monkeypatch.setattr(stage_tools, "_has_touchpoint", lambda thread_id: True)
        updates = []
        monkeypatch.setattr(stage_tools, "_update_lead_stage", lambda thread_id, new_stage: updates.append((thread_id, new_stage)))

        result = stage_tools.evaluate_stage_gate("thread-1")

        assert result["promoted"] is True
        assert result["gate"] == "G1"
        assert result["to_stage"] == "Seeker"
        assert result["reason"] == "touchpoint_recorded"
        assert updates == [("thread-1", "Seeker")]

    def test_gate_g1_blocks_when_touchpoint_missing(self, monkeypatch):
        import adk_agents.tools.l5_stage_tools as stage_tools

        monkeypatch.setattr(stage_tools, "_get_user_and_thread", lambda thread_id: {
            "thread_id": thread_id,
            "thread_name": "Lan",
            "page_id": "page-1",
            "lead_stage": "Intake",
        })
        monkeypatch.setattr(stage_tools, "_get_thread_messages", lambda thread_id: [])
        monkeypatch.setattr(stage_tools, "_has_touchpoint", lambda thread_id: False)
        monkeypatch.setattr(stage_tools, "_update_lead_stage", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not update stage")))

        result = stage_tools.evaluate_stage_gate("thread-1")

        assert result["promoted"] is False
        assert result["gate"] == "G1"
        assert result["reason"] == "missing_touchpoint"

    def test_gate_g3_promotes_when_contact_and_specific_program_exist(self, monkeypatch):
        import adk_agents.tools.l5_stage_tools as stage_tools

        messages = [
            {"sender": "Customer", "content": "Em muốn đăng ký lớp 4 tuần ở Hà Nội"},
            {"sender": "Customer", "content": "SĐT của em là 0912345678"},
        ]
        monkeypatch.setattr(stage_tools, "_get_user_and_thread", lambda thread_id: {
            "thread_id": thread_id,
            "thread_name": "Minh",
            "page_id": "page-1",
            "lead_stage": "Seeker",
            "phone": None,
            "email": None,
        })
        monkeypatch.setattr(stage_tools, "_get_thread_messages", lambda thread_id: messages)
        updates = []
        monkeypatch.setattr(stage_tools, "_update_lead_stage", lambda thread_id, new_stage: updates.append((thread_id, new_stage)))

        result = stage_tools.evaluate_stage_gate("thread-2")

        assert result["promoted"] is True
        assert result["gate"] == "G3"
        assert result["to_stage"] == "Seeker_Public_Program"
        assert result["reason"] == "valid_contact_and_program_detected"
        assert result["contact_type"] == "phone"
        assert updates == [("thread-2", "Seeker_Public_Program")]

    def test_gate_g3_blocks_when_specific_program_missing(self, monkeypatch):
        import adk_agents.tools.l5_stage_tools as stage_tools

        messages = [{"sender": "Customer", "content": "SĐT của em là 0912345678"}]
        monkeypatch.setattr(stage_tools, "_get_user_and_thread", lambda thread_id: {
            "thread_id": thread_id,
            "thread_name": "Minh",
            "page_id": "page-1",
            "lead_stage": "Seeker",
            "phone": None,
            "email": None,
        })
        monkeypatch.setattr(stage_tools, "_get_thread_messages", lambda thread_id: messages)
        monkeypatch.setattr(stage_tools, "_update_lead_stage", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not update stage")))

        result = stage_tools.evaluate_stage_gate("thread-2")

        assert result["promoted"] is False
        assert result["gate"] == "G3"
        assert result["reason"] == "missing_specific_program"
        assert result["contact_type"] == "phone"

    def test_gate_g3_blocks_when_valid_contact_missing(self, monkeypatch):
        import adk_agents.tools.l5_stage_tools as stage_tools

        messages = [{"sender": "Customer", "content": "Em muốn đăng ký lớp 4 tuần ở Hà Nội"}]
        monkeypatch.setattr(stage_tools, "_get_user_and_thread", lambda thread_id: {
            "thread_id": thread_id,
            "thread_name": "Minh",
            "page_id": "page-1",
            "lead_stage": "Seeker",
            "phone": None,
            "email": None,
        })
        monkeypatch.setattr(stage_tools, "_get_thread_messages", lambda thread_id: messages)
        monkeypatch.setattr(stage_tools, "_update_lead_stage", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not update stage")))

        result = stage_tools.evaluate_stage_gate("thread-3")

        assert result["promoted"] is False
        assert result["gate"] == "G3"
        assert result["reason"] == "missing_valid_contact"
        assert result["has_contact"] is False

    def test_registered_and_18_week_stages_are_manual_only(self, monkeypatch):
        import adk_agents.tools.l5_stage_tools as stage_tools

        monkeypatch.setattr(stage_tools, "_get_thread_messages", lambda thread_id: [])

        monkeypatch.setattr(stage_tools, "_get_user_and_thread", lambda thread_id: {
            "thread_id": thread_id,
            "thread_name": "Lan",
            "page_id": "page-1",
            "lead_stage": "Registered",
        })
        registered = stage_tools.evaluate_stage_gate("thread-registered")

        monkeypatch.setattr(stage_tools, "_get_user_and_thread", lambda thread_id: {
            "thread_id": thread_id,
            "thread_name": "Lan",
            "page_id": "page-1",
            "lead_stage": "18-Week Seeker",
        })
        deep = stage_tools.evaluate_stage_gate("thread-deep")

        assert registered["gate"] == "G4"
        assert registered["reason"] == "manual_only"
        assert deep["gate"] == "G5"
        assert deep["reason"] == "manual_only"
