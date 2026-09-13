"""Durable named Jarvis bots and their conversation bindings."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from events import BotCreated, bus

STATUSES = {"active", "paused", "archived"}
FIELDS = {"name", "role", "system_prompt", "brain", "tool_allowlist"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BotStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _db(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init(self):
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS bots (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
              system_prompt TEXT NOT NULL, brain TEXT NOT NULL DEFAULT 'gemini',
              tool_allowlist TEXT NOT NULL, memory_scope TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bot_conversations (
              bot_id TEXT NOT NULL UNIQUE, conversation_id INTEGER NOT NULL,
              created_at TEXT NOT NULL, FOREIGN KEY(bot_id) REFERENCES bots(id) ON DELETE CASCADE
            );
            """)

    @staticmethod
    def _row(row):
        if row is None:
            return None
        item = dict(row)
        item["tool_allowlist"] = json.loads(item["tool_allowlist"] or "[]")
        return item

    def create_bot(self, name, role, system_prompt, brain="gemini", tool_allowlist=None):
        bot_id, now = uuid.uuid4().hex, utc_now()
        with self._lock, self._db() as db:
            db.execute("INSERT INTO bots VALUES(?,?,?,?,?,?,?,?,?,?)", (
                bot_id, name.strip(), role.strip(), system_prompt.strip(), brain.strip() or "gemini",
                json.dumps(tool_allowlist or []), bot_id, "active", now, now,
            ))
        bus.emit(BotCreated(bot_id=bot_id))
        return self.get_bot(bot_id)

    def get_bot(self, bot_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
        if row is None:
            raise KeyError(bot_id)
        return self._row(row)

    def list_bots(self, status=None):
        with self._db() as db:
            if status:
                rows = db.execute("SELECT * FROM bots WHERE status=? ORDER BY created_at", (status,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM bots ORDER BY created_at").fetchall()
        return [self._row(row) for row in rows]

    def update_bot(self, bot_id, **fields):
        updates = {key: value for key, value in fields.items() if key in FIELDS and value is not None}
        if "tool_allowlist" in updates:
            updates["tool_allowlist"] = json.dumps(updates["tool_allowlist"])
        if updates:
            updates["updated_at"] = utc_now()
            sql = ",".join(f"{key}=?" for key in updates)
            with self._lock, self._db() as db:
                changed = db.execute(f"UPDATE bots SET {sql} WHERE id=?", (*updates.values(), bot_id)).rowcount
            if not changed:
                raise KeyError(bot_id)
        return self.get_bot(bot_id)

    def set_status(self, bot_id, status):
        if status not in STATUSES:
            raise ValueError("status must be active, paused, or archived")
        with self._lock, self._db() as db:
            changed = db.execute("UPDATE bots SET status=?,updated_at=? WHERE id=?", (status, utc_now(), bot_id)).rowcount
        if not changed:
            raise KeyError(bot_id)
        return self.get_bot(bot_id)

    def delete_bot(self, bot_id):
        with self._lock, self._db() as db:
            changed = db.execute("DELETE FROM bots WHERE id=?", (bot_id,)).rowcount
        if not changed:
            raise KeyError(bot_id)

    def conversation_id(self, bot_id, conversation_db):
        self.get_bot(bot_id)
        with self._lock, self._db() as db:
            row = db.execute("SELECT conversation_id FROM bot_conversations WHERE bot_id=?", (bot_id,)).fetchone()
            if row:
                return int(row[0])
            conversation_id = conversation_db.create_conversation(f"Bot {bot_id}", "auto", "general")
            db.execute("INSERT INTO bot_conversations VALUES(?,?,?)", (bot_id, conversation_id, utc_now()))
            return conversation_id

    def append_message(self, bot_id, conversation_db, role, content):
        conversation_id = self.conversation_id(bot_id, conversation_db)
        conversation_db.add_message(conversation_id, role, content)
        return conversation_id

    def close(self):
        return None
