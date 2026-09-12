"""Explicit facts in Jarvis's existing conversation database (single local owner)."""
import hashlib
import os
import re
import threading
from pathlib import Path

_lock = threading.RLock()


def facts(db, action, fact="", fact_id=""):
    from events import MemoryUpdated, bus
    owner = "local-owner"  # Server-owned scope; never supplied by the model.
    with _lock, db._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS explicit_facts (owner TEXT, id TEXT, text TEXT, PRIMARY KEY(owner,id))")
        conn.execute("BEGIN IMMEDIATE")
        if action == "remember_fact":
            fact = " ".join(fact.split())
            if not fact or len(fact) > 2000:
                raise ValueError("A fact must contain 1–2000 characters")
            if re.search(r"api[_ -]?key|password|bearer\s|private[_ -]?key|refresh[_ -]?token", fact, re.I):
                raise ValueError("Store credentials in Settings, not conversational memory")
            fact_id = hashlib.sha256(fact.casefold().encode()).hexdigest()[:24]
            conn.execute("INSERT OR IGNORE INTO explicit_facts VALUES (?,?,?)", (owner, fact_id, fact))
        elif action == "forget_fact":
            if not fact_id:
                raise ValueError("Recall facts first and supply the exact fact_id to forget")
            deleted = conn.execute("DELETE FROM explicit_facts WHERE owner=? AND id=?", (owner, fact_id)).rowcount
            if not deleted:
                return {"status": "not_found", "error": "No fact matches that ID"}
        rows = [{"id": i, "text": t} for i,t in conn.execute("SELECT id,text FROM explicit_facts WHERE owner=? ORDER BY id",(owner,))]
        conn.commit()
        directory = Path(db.db_path).parent / "memory-mirror"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "explicit-facts.md"
        temporary = target.with_suffix('.tmp')
        temporary.write_text("# Remembered facts\n\n" + "\n".join(f"- {r['text']}" for r in rows), encoding="utf-8")
        os.replace(temporary, target)
    if action != "recall_facts":
        bus.emit(MemoryUpdated(scope="facts", key=fact_id))
    return {"status": "success", "facts": rows, "fact_id": fact_id}
