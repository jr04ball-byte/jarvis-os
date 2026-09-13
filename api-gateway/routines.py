"""Durable interval/daily routines and their scheduler loop."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from events import RoutineTriggered, bus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def next_run(trigger_type: str, trigger_value: str, now: datetime | None = None) -> str:
    now = now or utc_now()
    if trigger_type == "interval":
        minutes = int(trigger_value)
        if not 1 <= minutes <= 525600:
            raise ValueError("interval must be 1–525600 minutes")
        return (now + timedelta(minutes=minutes)).isoformat()
    if trigger_type == "daily_at":
        try:
            hour, minute = map(int, trigger_value.split(":"))
            candidate = now.astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (ValueError, TypeError) as error:
            raise ValueError("daily_at must be HH:MM") from error
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("daily_at must be HH:MM")
        if candidate <= now.astimezone():
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc).isoformat()
    raise ValueError("trigger_type must be interval or daily_at")


class RoutineStore:
    def __init__(self, path: str | Path):
        self.path = str(path); self._lock = threading.RLock(); Path(self.path).parent.mkdir(parents=True, exist_ok=True); self._init()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30); db.row_factory = sqlite3.Row
        try: yield db; db.commit()
        finally: db.close()

    def _init(self):
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS routines (
              id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, goal_template TEXT NOT NULL,
              trigger_type TEXT NOT NULL, trigger_value TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
              last_run_at TEXT, next_run_at TEXT NOT NULL, last_run_project_id TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    @staticmethod
    def _row(row):
        item = dict(row); item["enabled"] = bool(item["enabled"]); return item

    def create(self, bot_id, goal_template, trigger_type, trigger_value, enabled=True):
        rid, now = uuid.uuid4().hex, utc_now()
        with self._lock, self._db() as db:
            db.execute("INSERT INTO routines VALUES(?,?,?,?,?,?,?,?,?,?,?)", (rid, bot_id, goal_template.strip(), trigger_type, trigger_value, int(enabled), None, next_run(trigger_type, trigger_value, now), None, now.isoformat(), now.isoformat()))
        return self.get(rid)

    def get(self, routine_id):
        with self._db() as db: row = db.execute("SELECT * FROM routines WHERE id=?", (routine_id,)).fetchone()
        if not row: raise KeyError(routine_id)
        return self._row(row)

    def list(self, bot_id):
        with self._db() as db: rows = db.execute("SELECT * FROM routines WHERE bot_id=? ORDER BY created_at", (bot_id,)).fetchall()
        return [self._row(row) for row in rows]

    def update(self, routine_id, **fields):
        current = self.get(routine_id)
        allowed = {k: v for k, v in fields.items() if k in {"goal_template", "trigger_type", "trigger_value", "enabled"} and v is not None}
        trigger_type = allowed.get("trigger_type", current["trigger_type"]); trigger_value = allowed.get("trigger_value", current["trigger_value"])
        if "trigger_type" in allowed or "trigger_value" in allowed: allowed["next_run_at"] = next_run(trigger_type, trigger_value)
        if "enabled" in allowed: allowed["enabled"] = int(allowed["enabled"])
        if allowed:
            allowed["updated_at"] = utc_now().isoformat(); sql = ",".join(f"{k}=?" for k in allowed)
            with self._lock, self._db() as db: db.execute(f"UPDATE routines SET {sql} WHERE id=?", (*allowed.values(), routine_id))
        return self.get(routine_id)

    def delete(self, routine_id):
        with self._lock, self._db() as db:
            if not db.execute("DELETE FROM routines WHERE id=?", (routine_id,)).rowcount: raise KeyError(routine_id)

    def delete_for_bot(self, bot_id):
        with self._lock, self._db() as db: db.execute("DELETE FROM routines WHERE bot_id=?", (bot_id,))

    def due(self):
        with self._db() as db: rows = db.execute("SELECT * FROM routines WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at", (utc_now().isoformat(),)).fetchall()
        return [self._row(row) for row in rows]

    def mark_run(self, routine_id, project_id):
        item, now = self.get(routine_id), utc_now()
        with self._lock, self._db() as db:
            db.execute("UPDATE routines SET last_run_at=?,next_run_at=?,last_run_project_id=?,updated_at=? WHERE id=?", (now.isoformat(), next_run(item["trigger_type"], item["trigger_value"], now), project_id, now.isoformat(), routine_id))
        return self.get(routine_id)

    def close(self): return None


async def scheduler_loop(routines, orchestrator, bots, poll_seconds=30):
    while True:
        for routine in routines.due():
            try:
                bot = bots.get_bot(routine["bot_id"])
                if bot["status"] != "active":
                    continue
                project = orchestrator.create_project(routine["goal_template"])
                routines.mark_run(routine["id"], project["id"])
                bus.emit(RoutineTriggered(routine_id=routine["id"], bot_id=bot["id"], project_id=project["id"]))
            except KeyError:
                routines.update(routine["id"], enabled=False)
        await asyncio.sleep(poll_seconds)
