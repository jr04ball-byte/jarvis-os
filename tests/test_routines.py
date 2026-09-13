import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[1] / "api-gateway"
sys.path.insert(0, str(GATEWAY)) if str(GATEWAY) not in sys.path else None

from bots import BotStore
from orchestrator import OrchestratorStore
from routines import RoutineStore, next_run, scheduler_loop


def test_routine_crud_and_dates(tmp_path):
    store = RoutineStore(tmp_path / "routines.db")
    item = store.create("bot", "Check inbox", "interval", "60")
    assert item["enabled"] is True and item["next_run_at"]
    assert store.update(item["id"], enabled=False)["enabled"] is False
    assert len(store.list("bot")) == 1
    store.delete(item["id"]); assert store.list("bot") == []
    now = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    assert next_run("daily_at", "12:00", now) > now.isoformat()


def test_scheduler_creates_project_for_due_routine(tmp_path):
    bot_store = BotStore(tmp_path / "bots.db"); routine_store = RoutineStore(tmp_path / "routines.db"); orchestrator = OrchestratorStore(tmp_path / "orchestrator.db")
    bot = bot_store.create_bot("Scout", "Research", "Research.")
    routine = routine_store.create(bot["id"], "Research updates", "interval", "1")
    with routine_store._db() as db: db.execute("UPDATE routines SET next_run_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", routine["id"]))

    async def run_once():
        task = asyncio.create_task(scheduler_loop(routine_store, orchestrator, bot_store, poll_seconds=60))
        await asyncio.sleep(.05); task.cancel()
        try: await task
        except asyncio.CancelledError: pass
    asyncio.run(run_once())
    assert routine_store.get(routine["id"])["last_run_project_id"]
    assert orchestrator.list_projects()
