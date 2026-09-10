"""Jarvis Orchestrator: deterministic goal -> plan -> tasks state machine.

This module deliberately has no LLM dependency. It provides durable project/task
state, task decomposition, routing hints, and audit-friendly transitions. The
existing agent/tool layer remains the execution authority.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workspace_registry import context_for

DEEP_RE = re.compile(r"\b(code|coding|debug|build|implement|refactor|architect|architecture|research|analy[sz]e|security|database|sql|python|javascript|typescript|docker|deploy|test|fix)\b", re.I)
TOOL_RE = re.compile(r"\b(send|email|gmail|calendar|schedule|remind|open|launch|create|delete|remove|cancel|control|turn on|turn off|play|pause|volume|file|folder|computer|screen|blender|home assistant)\b", re.I)

STATUS = {"pending", "ready", "running", "blocked", "awaiting_approval", "completed", "failed", "cancelled"}
TERMINAL = {"completed", "failed", "cancelled"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_goal(goal: str) -> dict[str, Any]:
    text = (goal or "").strip()
    lower = text.lower()
    if not text:
        raise ValueError("goal is required")
    if DEEP_RE.search(text):
        complexity = "deep"
        brain = "qwen_or_opencode"
    else:
        complexity = "fast"
        brain = "gemini_or_gemma"
    execution = "tools" if TOOL_RE.search(text) else "conversation"
    return {"complexity": complexity, "brain": brain, "execution": execution}


def build_plan(goal: str) -> dict[str, Any]:
    """Create a durable, capability-aware plan from a natural-language goal."""
    meta = classify_goal(goal)
    target = context_for(goal)
    tasks: list[dict[str, Any]] = [
        {"title": "Understand the goal", "kind": "reason", "risk": "read", "brain": meta["brain"], "capability": "reason"},
        {"title": "Inspect available context and resources", "kind": "inspect", "risk": "read", "brain": "local", "capability": "inspect"},
    ]
    if target.get("target"):
        tasks.append({"title": f"Inspect {target['workspace']['label']} workspace", "kind": "workspace_inspect", "risk": "read", "brain": "local", "capability": "workspace_inspect"})
    if meta["execution"] == "tools":
        tasks.append({"title": "Prepare required actions", "kind": "plan_actions", "risk": "write", "brain": "orchestrator", "capability": "plan"})
        tasks.append({"title": "Execute approved actions", "kind": "execute", "risk": "write", "brain": "tool_executor", "capability": "execute"})
    else:
        tasks.append({"title": "Produce the result", "kind": "respond", "risk": "read", "brain": meta["brain"], "capability": "respond"})
    tasks.append({"title": "Verify the result", "kind": "verify", "risk": "read", "brain": "local", "capability": "verify"})
    return {"goal": goal.strip(), "routing": meta, "target": target, "tasks": tasks}


class OrchestratorStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    @contextmanager
    def _db(self):
        c = self._connect()
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def close(self):
        """Release any lingering SQLite handles.

        Connections are opened per-operation and closed immediately, but
        unfinalized cursors can keep the db file locked on Windows until
        cyclic GC runs. Call before deleting the db file (e.g. temp-dir
        cleanup in tests).
        """
        import gc
        gc.collect()

    def _init(self):
        with self._db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS jarvis_projects (
                id TEXT PRIMARY KEY, goal TEXT NOT NULL, plan_json TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jarvis_tasks (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, position INTEGER NOT NULL,
                title TEXT NOT NULL, kind TEXT NOT NULL, risk TEXT NOT NULL,
                brain TEXT NOT NULL, capability TEXT NOT NULL DEFAULT 'generic', status TEXT NOT NULL, depends_on TEXT NOT NULL,
                result_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES jarvis_projects(id)
            );
            CREATE TABLE IF NOT EXISTS jarvis_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, task_id TEXT,
                event TEXT NOT NULL, detail_json TEXT, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jarvis_tasks_project ON jarvis_tasks(project_id, position);
            
            CREATE INDEX IF NOT EXISTS idx_jarvis_audit_project ON jarvis_audit(project_id, id);
            """)
        with self._db() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(jarvis_tasks)").fetchall()}
            if "capability" not in cols:
                c.execute("ALTER TABLE jarvis_tasks ADD COLUMN capability TEXT NOT NULL DEFAULT 'generic'")

    def _audit(self, c, project_id, task_id, event, detail=None):
        c.execute("INSERT INTO jarvis_audit(project_id,task_id,event,detail_json,created_at) VALUES(?,?,?,?,?)",
                  (project_id, task_id, event, json.dumps(detail or {}, default=str), utc_now()))

    def create_project(self, goal: str) -> dict[str, Any]:
        plan = build_plan(goal)
        pid = uuid.uuid4().hex
        now = utc_now()
        with self._lock, self._connect() as c:
            c.execute("INSERT INTO jarvis_projects VALUES(?,?,?,?,?,?)", (pid, goal.strip(), json.dumps(plan), "active", now, now))
            prev = None
            for i, task in enumerate(plan["tasks"]):
                tid = uuid.uuid4().hex
                deps = [prev] if prev else []
                c.execute("INSERT INTO jarvis_tasks(id,project_id,position,title,kind,risk,brain,capability,status,depends_on,result_json,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (tid, pid, i, task["title"], task["kind"], task["risk"], task["brain"], task.get("capability", "generic"),
                           "ready" if not deps else "pending", json.dumps(deps), None, None, now, now))
                prev = tid
            self._audit(c, pid, None, "project_created", {"goal": goal, "task_count": len(plan["tasks"])})
        return self.get_project(pid)

    def _task_dict(self, row):
        d = dict(row)
        d["depends_on"] = json.loads(d["depends_on"] or "[]")
        if d.get("result_json"):
            try: d["result"] = json.loads(d["result_json"])
            except Exception: d["result"] = d["result_json"]
        d.pop("result_json", None)
        return d

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._db() as c:
            p = c.execute("SELECT * FROM jarvis_projects WHERE id=?", (project_id,)).fetchone()
            if not p: raise KeyError(project_id)
            tasks = [self._task_dict(x) for x in c.execute("SELECT * FROM jarvis_tasks WHERE project_id=? ORDER BY position", (project_id,)).fetchall()]
            return {"id": p["id"], "goal": p["goal"], "status": p["status"], "plan": json.loads(p["plan_json"]), "tasks": tasks,
                    "created_at": p["created_at"], "updated_at": p["updated_at"]}

    def next_task(self, project_id: str) -> dict[str, Any] | None:
        with self._db() as c:
            rows = c.execute("SELECT * FROM jarvis_tasks WHERE project_id=? ORDER BY position", (project_id,)).fetchall()
            for r in rows:
                if r["status"] != "pending" and r["status"] != "ready": continue
                deps = json.loads(r["depends_on"] or "[]")
                if all(c.execute("SELECT status FROM jarvis_tasks WHERE id=?", (d,)).fetchone()[0] == "completed" for d in deps):
                    return self._task_dict(r)
        return None

    def transition(self, task_id: str, status: str, result: Any = None, error: str | None = None) -> dict[str, Any]:
        if status not in STATUS: raise ValueError(f"invalid status: {status}")
        now = utc_now()
        with self._lock, self._connect() as c:
            row = c.execute("SELECT * FROM jarvis_tasks WHERE id=?", (task_id,)).fetchone()
            if not row: raise KeyError(task_id)
            if row["status"] in TERMINAL and status != row["status"]: raise ValueError("terminal task cannot transition")
            c.execute("UPDATE jarvis_tasks SET status=?, result_json=?, error=?, updated_at=? WHERE id=?",
                      (status, json.dumps(result, default=str) if result is not None else None, error, now, task_id))
            c.execute("UPDATE jarvis_projects SET updated_at=? WHERE id=?", (now, row["project_id"]))
            self._audit(c, row["project_id"], task_id, "task_transition", {"from": row["status"], "to": status, "error": error})
            if status == "completed":
                nxt = c.execute("SELECT * FROM jarvis_tasks WHERE project_id=? AND position>? ORDER BY position LIMIT 1", (row["project_id"], row["position"])).fetchone()
                if nxt and nxt["status"] == "pending":
                    c.execute("UPDATE jarvis_tasks SET status='ready', updated_at=? WHERE id=?", (now, nxt["id"]))
            statuses = [x[0] for x in c.execute("SELECT status FROM jarvis_tasks WHERE project_id=?", (row["project_id"],)).fetchall()]
            if statuses and all(s == "completed" for s in statuses):
                c.execute("UPDATE jarvis_projects SET status='completed', updated_at=? WHERE id=?", (now, row["project_id"]))
        return self.get_project(row["project_id"])

    def list_projects(self, limit: int = 20) -> list[dict[str, Any]]:
        """Dashboard-safe recent project summaries with task status counts."""
        cap = max(1, min(int(limit), 100))
        with self._db() as c:
            rows = c.execute(
                "SELECT * FROM jarvis_projects ORDER BY updated_at DESC LIMIT ?", (cap,)
            ).fetchall()
            out: list[dict[str, Any]] = []
            for p in rows:
                counts = {r[0]: r[1] for r in c.execute(
                    "SELECT status, COUNT(*) FROM jarvis_tasks WHERE project_id=? GROUP BY status",
                    (p["id"],),
                ).fetchall()}
                out.append({
                    "id": p["id"], "goal": p["goal"], "status": p["status"],
                    "task_counts": counts, "created_at": p["created_at"], "updated_at": p["updated_at"],
                })
            return out

    def audit(self, project_id: str, limit: int = 100):
        with self._db() as c:
            rows = c.execute("SELECT * FROM jarvis_audit WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, max(1, min(limit, 500)))).fetchall()
            return [dict(x) for x in rows]
