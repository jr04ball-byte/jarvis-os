import os
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

from orchestrator import OrchestratorStore, build_plan
from workspace_registry import match_target


def test_email_agent_goal_gets_workspace_workflow():
    plan = build_plan("Get Email Agent ready for production")
    assert plan["target"]["target"] == "email_agent"
    assert [t["kind"] for t in plan["tasks"]] == [
        "reason", "inspect", "workspace_inspect", "plan_actions", "execute", "verify"
    ]


def test_target_matching_is_specific():
    assert match_target("fix the ai workforce overseer") == "ai_workforce"
    assert match_target("improve outbound-ai call bot") == "outbound_ai"
    assert match_target("tell me a joke") is None


def test_store_persists_capability_metadata_and_resumes_dependencies():
    with tempfile.TemporaryDirectory() as d:
        store = OrchestratorStore(os.path.join(d, "orchestrator.db"))
        project = store.create_project("Get Email Agent ready for production")
        assert project["tasks"][0]["capability"] == "reason"
        assert store.next_task(project["id"])["kind"] == "reason"
        store.transition(project["tasks"][0]["id"], "completed", {"ok": True})
        assert store.next_task(project["id"])["kind"] == "inspect"
        store.close()

def test_project_target_survives_store_round_trip():
    with tempfile.TemporaryDirectory() as d:
        store = OrchestratorStore(os.path.join(d, "orchestrator.db"))
        project = store.create_project("Get Email Agent ready for production")
        loaded = store.get_project(project["id"])
        assert loaded["plan"]["target"]["target"] == "email_agent"
        assert loaded["plan"]["target"]["workspace"]["label"] == "Email Agent"
        store.close()
