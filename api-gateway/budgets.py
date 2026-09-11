"""V25 budget guards: daily token caps for metered (cloud) providers.

Local providers (Ollama) and the worker runtime cost nothing per token and
are never gated. Cloud providers share one daily token budget that resets
at UTC midnight; when exhausted, callers get HTTP 429 instead of a bill.

Env:
  JARVIS_DAILY_TOKEN_BUDGET - total metered tokens per UTC day (default 50000,
    roughly 50-100 ordinary chat turns). "0" disables enforcement.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

METERED_KINDS = {"cloud"}
DEFAULT_DAILY_BUDGET = 50_000


def daily_budget() -> int:
    try:
        return max(0, int(os.getenv("JARVIS_DAILY_TOKEN_BUDGET", str(DEFAULT_DAILY_BUDGET))))
    except ValueError:
        return DEFAULT_DAILY_BUDGET


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _db_path() -> str:
    base = os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data"))
    Path(base).mkdir(parents=True, exist_ok=True)
    return os.path.join(base, "budgets.db")


def normalize_usage(usage: dict[str, Any] | None) -> tuple[int, int]:
    """Return (prompt_tokens, completion_tokens) for Gemini or OpenAI shapes."""
    usage = usage or {}
    prompt = usage.get("prompt_tokens", usage.get("promptTokenCount", 0)) or 0
    completion = usage.get("completion_tokens", usage.get("candidatesTokenCount", 0)) or 0
    try:
        return max(0, int(prompt)), max(0, int(completion))
    except (TypeError, ValueError):
        return 0, 0


class DailyBudget:
    """Tiny SQLite ledger: one row per (UTC day, provider)."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _db_path()
        self._lock = threading.RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS daily_usage ("
                "day TEXT NOT NULL, provider TEXT NOT NULL, "
                "prompt_tokens INTEGER NOT NULL DEFAULT 0, "
                "completion_tokens INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (day, provider))")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _spent(self, conn, day: str) -> int:
        rows = conn.execute(
            "SELECT prompt_tokens, completion_tokens FROM daily_usage WHERE day=?",
            (day,)).fetchall()
        return sum((p or 0) + (c or 0) for p, c in rows)

    def spent_today(self) -> int:
        with self._lock:
            with self._connect() as conn:
                return self._spent(conn, _today())

    def remaining(self) -> int | None:
        budget = daily_budget()
        if budget <= 0:
            return None
        return max(0, budget - self.spent_today())

    def check(self, provider: str, kind: str) -> None:
        """Raise 429 if a metered call would exceed today's budget. No-op otherwise."""
        if kind not in METERED_KINDS:
            return
        remaining = self.remaining()
        if remaining is not None and remaining <= 0:
            raise HTTPException(
                429,
                f"daily cloud token budget exhausted ({daily_budget()} tokens/day); "
                "resets at UTC midnight or raise JARVIS_DAILY_TOKEN_BUDGET")

    def record(self, provider: str, kind: str, usage: dict[str, Any] | None) -> tuple[int, int]:
        """Add a call's token usage to today's ledger. Returns (prompt, completion)."""
        prompt, completion = normalize_usage(usage)
        if kind not in METERED_KINDS or (prompt <= 0 and completion <= 0):
            return prompt, completion
        day = _today()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO daily_usage(day, provider, prompt_tokens, completion_tokens)"
                    " VALUES(?,?,?,?)"
                    " ON CONFLICT(day, provider) DO UPDATE SET"
                    " prompt_tokens=prompt_tokens+excluded.prompt_tokens,"
                    " completion_tokens=completion_tokens+excluded.completion_tokens",
                    (day, provider, prompt, completion))
        return prompt, completion


budget = DailyBudget()
"""Process-global ledger (tests construct their own DailyBudget on temp paths)."""
