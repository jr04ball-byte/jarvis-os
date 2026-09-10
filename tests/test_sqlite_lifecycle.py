"""Cycle-1 regression: SQLite stores must release file handles (Windows).

Proven failure mode (test_v201_autopilot, WinError 32): connections opened
with `sqlite3.connect(...) as conn` commit on exit but never close, so
temp-dir cleanup fails while the store is still alive. These tests pin the
fixed lifecycle for every store class.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import main
from project_worker import WorkerRunStore


def test_conversation_db_temp_dir_lifecycle():
    with tempfile.TemporaryDirectory() as d:
        db = main.ConversationDB(db_path=os.path.join(d, "conversations.db"))
        cid = db.create_conversation("lifecycle check", "test-model")
        assert db.conversation_exists(cid)


def test_document_rag_temp_dir_lifecycle():
    with tempfile.TemporaryDirectory() as d:
        rag = main.DocumentRAG(db_path=os.path.join(d, "conversations.db"))
        rag.add_document("Jarvis lifecycle regression document", "doc-1")
        assert "doc-1" in rag.documents


def test_worker_run_store_temp_dir_lifecycle():
    with tempfile.TemporaryDirectory() as d:
        store = WorkerRunStore(os.path.join(d, "worker_runs.db"))
        run = store.create("email_agent", "email-agent", "lifecycle check", {"branch": "test"})
        assert store.get(run["id"])["id"] == run["id"]
        assert any(r["id"] == run["id"] for r in store.list())
