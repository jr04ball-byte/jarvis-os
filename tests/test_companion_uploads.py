import hashlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api-gateway'))


def client(monkeypatch, tmp_path, max_bytes=1024):
    from main import create_app
    from routes import companion

    monkeypatch.setattr(companion, 'AI_API_TOKEN', 'phone-test-token')
    monkeypatch.setattr(companion, 'PHONE_UPLOAD_DIR', tmp_path.resolve())
    monkeypatch.setattr(companion, 'PHONE_UPLOAD_MAX_BYTES', max_bytes)
    return TestClient(create_app()), {'Authorization': 'Bearer phone-test-token'}


def test_phone_upload_requires_companion_token(monkeypatch, tmp_path):
    app, _ = client(monkeypatch, tmp_path)
    with app:
        assert app.post('/v1/companion/upload', files={'file': ('note.txt', b'hello')}).status_code == 401


def test_phone_upload_is_safe_hashed_listed_and_downloadable(monkeypatch, tmp_path):
    app, headers = client(monkeypatch, tmp_path)
    body = b'hello from iphone'
    with app:
        first = app.post('/v1/companion/upload', headers=headers,
                         data={'folder': '../Photos'}, files={'file': ('../../note.txt', body)})
        assert first.status_code == 200
        result = first.json()
        assert result['relative_path'] == 'Photos/note.txt'
        assert result['sha256'] == hashlib.sha256(body).hexdigest()
        assert (tmp_path / 'Photos/note.txt').read_bytes() == body

        second = app.post('/v1/companion/upload', headers=headers,
                          data={'folder': '../Photos'}, files={'file': ('note.txt', body)})
        assert second.json()['relative_path'] == 'Photos/note (2).txt'

        listing = app.get('/v1/companion/files', headers=headers).json()
        assert [row['name'] for row in listing['files']] == ['note (2).txt', 'note.txt']
        downloaded = app.get('/v1/companion/files/Photos/note.txt', headers=headers)
        assert downloaded.content == body


def test_empty_project_recovery_is_clean(tmp_path):
    from orchestrator import OrchestratorStore
    assert OrchestratorStore(str(tmp_path / 'empty.db')).recover() == []


def test_phone_companions_expose_file_picker_and_upload_contract():
    html = (ROOT / 'api-gateway/companion.html').read_text(encoding='utf-8')
    swift = (ROOT / 'ios-companion/AICompanion.swift').read_text(encoding='utf-8')
    assert 'type="file" multiple' in html
    assert "'/v1/companion/upload'" in html
    assert '.fileImporter(' in swift
    assert 'v1/companion/upload' in swift


def test_phone_upload_limit_removes_partial_file(monkeypatch, tmp_path):
    app, headers = client(monkeypatch, tmp_path, max_bytes=4)
    with app:
        response = app.post('/v1/companion/upload', headers=headers,
                            files={'file': ('large.bin', b'12345')})
    assert response.status_code == 413
    assert not list(tmp_path.rglob('*uploading'))
    assert not (tmp_path / 'Inbox/large.bin').exists()
