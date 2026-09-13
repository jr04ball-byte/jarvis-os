import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

GATEWAY = Path(__file__).resolve().parents[1] / "api-gateway"
sys.path.insert(0, str(GATEWAY)) if str(GATEWAY) not in sys.path else None

from bots import BotStore
from fact_memory import facts
from store import ConversationDB
from schemas import ChatMessage
from services import apply_system_prompt


def test_bot_crud_and_conversation(tmp_path):
    store = BotStore(tmp_path / "bots.db")
    conversations = ConversationDB(str(tmp_path / "conversations.db"))
    bot = store.create_bot("Scout", "Research", "Find grounded facts.", tool_allowlist=["research_search"])
    assert bot["status"] == "active" and bot["memory_scope"] == bot["id"]
    assert store.list_bots()[0]["tool_allowlist"] == ["research_search"]
    assert store.update_bot(bot["id"], role="News")["role"] == "News"
    assert store.set_status(bot["id"], "paused")["status"] == "paused"
    first = store.conversation_id(bot["id"], conversations)
    assert store.conversation_id(bot["id"], conversations) == first
    store.delete_bot(bot["id"])
    with pytest.raises(KeyError): store.get_bot(bot["id"])


def test_fact_memory_isolated_by_owner(tmp_path):
    conversations = ConversationDB(str(tmp_path / "conversations.db"))
    facts(conversations, "remember_fact", fact="Likes concise reports", owner="bot-a")
    assert len(facts(conversations, "recall_facts", owner="bot-a")["facts"]) == 1
    assert facts(conversations, "recall_facts", owner="bot-b")["facts"] == []
    assert len(facts(conversations, "recall_facts", owner="global")["facts"]) == 0


def test_bot_status_validation(tmp_path):
    store = BotStore(tmp_path / "bots.db")
    bot = store.create_bot("A", "B", "C")
    with pytest.raises(ValueError): store.set_status(bot["id"], "broken")


def test_bot_prompt_keeps_core_safety_rules():
    messages = apply_system_prompt([ChatMessage(role="user", content="hello")], "general", "You triage the inbox.")
    assert "BOT ROLE\nYou triage the inbox." in messages[0].content
    assert "Never claim that a tool" in messages[0].content


def test_bot_http_crud_memory_routine_and_skill(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_REQUIRE_AUTH", "false")
    import main
    from deps import container
    from orchestrator import OrchestratorStore
    from routines import RoutineStore
    from skills import SkillStore

    container.override("db", ConversationDB(str(tmp_path / "conversations.db")))
    container.override("bots", BotStore(tmp_path / "bots.db"))
    container.override("routines", RoutineStore(tmp_path / "routines.db"))
    container.override("skills", SkillStore(tmp_path / "skills.db"))
    container.override("orchestrator", OrchestratorStore(tmp_path / "orchestrator.db"))
    try:
        with TestClient(main.create_app()) as client:
            bot = client.post("/v1/bots", json={"name": "Scout", "role": "Research", "system_prompt": "Find facts.", "tool_allowlist": ["research_search"]}).json()
            assert client.get("/v1/bots").json()["bots"][0]["id"] == bot["id"]
            assert client.post(f"/v1/bots/{bot['id']}/memory", json={"fact": "Prefers concise reports"}).status_code == 200
            routine = client.post(f"/v1/bots/{bot['id']}/routines", json={"goal_template": "Check updates", "trigger_type": "interval", "trigger_value": "60"}).json()
            assert client.post(f"/v1/bots/{bot['id']}/routines/{routine['id']}/run").status_code == 200
            assert client.post(f"/v1/bots/{bot['id']}/skills/record/start", json={"name": "Daily"}).status_code == 200
            assert client.post(f"/v1/bots/{bot['id']}/skills/record/stop").json()["name"] == "Daily"
            assert client.delete(f"/v1/bots/{bot['id']}").status_code == 200
    finally:
        for name in ("db", "bots", "routines", "skills", "orchestrator"):
            container.reset(name)
