"""V24 P1: application singletons (first step of the DI container).

Data paths resolve identically to the old main.py import-time code:
`API_DATA_DIR` env or `<api-gateway>/data`. main.py re-exports every name
for backward compatibility (uvicorn entry, tests).
"""
from __future__ import annotations

import os
from pathlib import Path

from orchestrator import OrchestratorStore
from project_worker import WorkerRunStore
from store import ConversationDB, DocumentRAG, PerformanceMonitor


def data_dir() -> Path:
    return Path(os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data")))


ORCHESTRATOR_DB = data_dir() / "orchestrator.db"
PROJECT_WORKER_DB = ORCHESTRATOR_DB.parent / "project_worker.db"

db = ConversationDB()
rag = DocumentRAG()
monitor = PerformanceMonitor()
orchestrator = OrchestratorStore(ORCHESTRATOR_DB)
project_worker_runs = WorkerRunStore(str(PROJECT_WORKER_DB))
