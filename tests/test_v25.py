import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api-gateway'))
sys.path.insert(0, str(ROOT / 'tools'))
from activity import ActivityCore
from adopt_v25 import plan, stage
from events import Event, EventBus, WorkerCompleted
from maintenance import MaintenanceWorker, mirror_database


def test_activity_redacts_bounds_and_classifies():
    bus = EventBus()
    core = ActivityCore(bus)
    core.start()
    for _ in range(205):
        bus.emit(Event(name='provider.selected', payload={'secret': 'never expose'}))
    bus.emit(WorkerCompleted(status='blocked_environment'))
    snap = core.snapshot()
    assert len(snap['events']) == 200
    assert snap['state'] == 'blocked'
    assert 'secret' not in json.dumps(snap)
    assert core.snapshot(1)['truncated']
    core.close()
    bus.emit(Event(name='ignored'))
    assert core.snapshot()['sequence'] == 206


def test_maintenance_backup_and_mirror(tmp_path):
    source = tmp_path / 'conversations.db'
    with sqlite3.connect(source) as db:
        db.executescript("CREATE TABLE conversations(id INTEGER,title TEXT); CREATE TABLE messages(id INTEGER,conversation_id INTEGER,role TEXT,content TEXT); CREATE TABLE rag_documents(doc_id TEXT,content TEXT); INSERT INTO conversations VALUES(1,'Hello'); INSERT INTO messages VALUES(1,1,'user','Remember this'); INSERT INTO rag_documents VALUES('../../escape','Safe path');")
    worker = MaintenanceWorker(tmp_path, EventBus())
    assert worker.cycle()['state'] == 'completed'
    assert 'Remember this' in (tmp_path / 'memory-mirror/conversation-1.md').read_text()
    assert (tmp_path / 'maintenance-backups/conversations.db').exists()
    assert not (tmp_path.parent / 'escape.md').exists()
    with sqlite3.connect(source) as db:
        db.execute('DELETE FROM conversations')
    mirror_database(source, tmp_path / 'memory-mirror')
    assert not (tmp_path / 'memory-mirror/conversation-1.md').exists()


def test_corrupt_database_reports_failure(tmp_path):
    (tmp_path / 'bad.db').write_text('not sqlite')
    assert MaintenanceWorker(tmp_path, EventBus()).cycle()['state'] == 'failed'


def test_upgrade_stale_plan_and_secrets(tmp_path):
    current, release = tmp_path / 'current', tmp_path / 'release'
    current.mkdir(); release.mkdir()
    (current / '.env').write_text('SECRET=private')
    (current / 'app.py').write_text('old')
    (release / 'app.py').write_text('new')
    approved = plan(current, release)
    (release / 'app.py').write_text('changed')
    with pytest.raises(ValueError):
        stage(current, release, tmp_path / 'candidate', approved)
    stage(current, release, tmp_path / 'candidate', plan(current, release))
    assert (current / 'app.py').read_text() == 'old'
    assert not (tmp_path / 'candidate/.env').exists()
    with pytest.raises(ValueError):
        stage(current, release, tmp_path / 'candidate', plan(current, release))


def test_companion_auth_and_voice_exclusions(monkeypatch, tmp_path):
    import main
    from fastapi.testclient import TestClient
    from routes import companion
    monkeypatch.setattr(main, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(main, 'AI_REQUIRE_AUTH', False)
    monkeypatch.setattr(companion, 'AI_API_TOKEN', 'test-secret')
    with TestClient(main.create_app()) as client:
        assert client.get('/v1/companion/status').status_code == 401
        result = client.get('/v1/companion/status', headers={'Authorization': 'Bearer test-secret'})
        assert result.status_code == 200
        assert result.json()['audio_output'] is False
        for path in ['/v1/deepgram-speak', '/v1/deepgram-speak-stream', '/v1/gemini/live-token']:
            assert client.post(path, json={}).status_code == 410
        assert 'Hold to dictate' in client.get('/voice-live').text
        assert client.get('/v1/activity').status_code == 200
    monkeypatch.setattr(companion, 'AI_API_TOKEN', '')
    with TestClient(main.create_app()) as client:
        assert client.get('/v1/companion/status').status_code == 503
