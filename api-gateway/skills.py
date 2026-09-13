"""Recorded Jarvis tool-call sequences."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from events import SkillRecorded, SkillReplayed, bus


def utc_now(): return datetime.now(timezone.utc).isoformat()


class SkillStore:
    def __init__(self, path: str | Path):
        self.path = str(path); self._lock = threading.RLock(); self._recordings = {}; Path(self.path).parent.mkdir(parents=True, exist_ok=True); self._init()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30); db.row_factory = sqlite3.Row
        try: yield db; db.commit()
        finally: db.close()

    def _init(self):
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS skills (
              id TEXT PRIMARY KEY, bot_id TEXT, name TEXT NOT NULL, description TEXT NOT NULL,
              steps_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    @staticmethod
    def _row(row):
        if not row: return None
        item = dict(row); item["steps"] = json.loads(item.pop("steps_json")); return item

    def start_recording(self, bot_id, name, description=""):
        recording_id = uuid.uuid4().hex
        with self._lock: self._recordings[bot_id] = {"recording_id": recording_id, "name": name.strip(), "description": description.strip(), "steps": []}
        return {"status": "recording", **self._recordings[bot_id]}

    def is_recording(self, bot_id):
        with self._lock: return bot_id in self._recordings

    def record_step(self, bot_id, tool, arguments):
        with self._lock:
            if bot_id in self._recordings:
                self._recordings[bot_id]["steps"].append({"tool": tool, "arguments": arguments})

    def stop_recording(self, bot_id):
        with self._lock: recording = self._recordings.pop(bot_id, None)
        if not recording: raise KeyError("no active recording")
        skill_id, now = uuid.uuid4().hex, utc_now()
        with self._db() as db:
            db.execute("INSERT INTO skills VALUES(?,?,?,?,?,?,?)", (skill_id, bot_id, recording["name"], recording["description"], json.dumps(recording["steps"]), now, now))
        bus.emit(SkillRecorded(skill_id=skill_id, bot_id=bot_id))
        return self.get(skill_id)

    def create(self, name, description, steps, bot_id=None):
        skill_id, now = uuid.uuid4().hex, utc_now()
        with self._db() as db: db.execute("INSERT INTO skills VALUES(?,?,?,?,?,?,?)", (skill_id, bot_id, name, description, json.dumps(steps), now, now))
        bus.emit(SkillRecorded(skill_id=skill_id, bot_id=bot_id or "global")); return self.get(skill_id)

    def get(self, skill_id):
        with self._db() as db: row = db.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        if not row: raise KeyError(skill_id)
        return self._row(row)

    def list(self, bot_id):
        with self._db() as db: rows = db.execute("SELECT * FROM skills WHERE bot_id=? OR bot_id IS NULL ORDER BY created_at", (bot_id,)).fetchall()
        return [self._row(row) for row in rows]

    def delete_for_bot(self, bot_id):
        with self._db() as db: db.execute("DELETE FROM skills WHERE bot_id=?", (bot_id,))

    def replayed(self, skill_id, bot_id):
        bus.emit(SkillReplayed(skill_id=skill_id, bot_id=bot_id or "global"))

    def close(self): return None
