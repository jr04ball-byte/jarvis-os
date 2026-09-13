import sys
from pathlib import Path

import pytest

GATEWAY = Path(__file__).resolve().parents[1] / "api-gateway"
sys.path.insert(0, str(GATEWAY)) if str(GATEWAY) not in sys.path else None

from skills import SkillStore


def test_record_and_list_skill(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    store.start_recording("bot", "Morning", "Open daily tools")
    store.record_step("bot", "computer_open", {"target": "settings"})
    skill = store.stop_recording("bot")
    assert skill["steps"] == [{"tool": "computer_open", "arguments": {"target": "settings"}}]
    assert store.list("bot")[0]["id"] == skill["id"]


def test_recording_state_and_shared_skill(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    shared = store.create("Search", "Search safely", [{"tool": "research_search", "arguments": {"query": "news"}}])
    assert shared["bot_id"] is None
    assert store.list("any-bot")[0]["id"] == shared["id"]
    with pytest.raises(KeyError): store.stop_recording("missing")
