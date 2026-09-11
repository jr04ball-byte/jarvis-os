"""Local maintenance: SQLite checks/backups and a derived Markdown mirror.

Does not execute model-generated commands, install packages or modify source.
"""
import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from events import Event


def atomic_write(path, text):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def mirror_database(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    # One read transaction gives a consistent export while writers continue.
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        files = {}
        if {'conversations', 'messages'} <= tables:
            for ident, title in conn.execute('SELECT id, title FROM conversations ORDER BY id'):
                parts = [f'# Conversation {ident}', '', str(title or ''), '']
                for role, content in conn.execute('SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id', (ident,)):
                    parts.extend([f'## {role}', '', str(content or ''), ''])
                files[f'conversation-{ident}.md'] = '\n'.join(parts)
        if 'rag_documents' in tables:
            for ident, content in conn.execute('SELECT doc_id, content FROM rag_documents'):
                key = hashlib.sha256(str(ident).encode()).hexdigest()
                files[f'document-{key}.md'] = f'# {ident}\n\n{content}\n'
    for name, content in files.items():
        atomic_write(destination / name, content)
    # Remove only files recorded as owned by the previous mirror generation.
    manifest = destination / 'manifest.json'
    previous = json.loads(manifest.read_text()) if manifest.exists() else []
    for name in set(previous) - files.keys():
        if Path(name).name == name and name.endswith('.md'):
            (destination / name).unlink(missing_ok=True)
    atomic_write(manifest, json.dumps(sorted(files)))
    return len(files)


class MaintenanceWorker:
    def __init__(self, directory, bus, interval=300):
        self.directory = Path(directory).resolve()
        self.bus = bus
        self.interval = max(30, interval)
        self.lock = threading.Lock()
        self.status = {'state': 'idle', 'last_run': None}
        self.stop = asyncio.Event()

    def cycle(self):
        if not self.lock.acquire(blocking=False):
            return {'state': 'busy'}
        try:
            self.status = {'state': 'running', 'last_run': time.time()}
            self.bus.emit(Event(name='maintenance.started'))
            self.directory.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self.directory).free < 128 * 1024 * 1024:
                raise OSError('Insufficient free disk space')
            backups = self.directory / 'maintenance-backups'
            backups.mkdir(exist_ok=True)
            checked, mirrored = [], 0
            for source in sorted(self.directory.glob('*.db')):
                with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
                    if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise OSError('Database integrity check failed')
                    temp = backups / (source.name + '.tmp')
                    with closing(sqlite3.connect(temp)) as target:
                        conn.backup(target)
                    os.replace(temp, backups / source.name)
                checked.append(source.name)
                if source.name == 'conversations.db':
                    mirrored = mirror_database(source, self.directory / 'memory-mirror')
            self.status = dict(state='completed', last_run=time.time(), databases=checked, mirrored=mirrored)
            self.bus.emit(Event(name='maintenance.completed'))
        except Exception:
            logging.getLogger(__name__).exception('Local maintenance failed')
            self.status = dict(state='failed', last_run=time.time(), detail='Maintenance failed; inspect local storage and retry.')
            self.bus.emit(Event(name='maintenance.failed'))
        finally:
            self.lock.release()
        return dict(self.status)

    async def run(self):
        while not self.stop.is_set():
            await asyncio.to_thread(self.cycle)
            try:
                await asyncio.wait_for(self.stop.wait(), self.interval)
            except TimeoutError:
                pass
