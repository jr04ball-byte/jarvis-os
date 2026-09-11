"""V24 P6: google_oauth.TokenStore round-trip (offline, temp dirs)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import google_oauth
from cryptography.fernet import Fernet


def make_store(monkeypatch, tmp_path):
    monkeypatch.setattr(google_oauth, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return google_oauth.TokenStore(data_dir=str(tmp_path))


def test_save_load_list_delete_round_trip(monkeypatch, tmp_path):
    store = make_store(monkeypatch, tmp_path)
    assert store.list_accounts() == []
    store.save_tokens("a@b.c", {"access_token": "sekret", "scope": "gmail.readonly"})
    assert store.list_accounts() == ["a@b.c"]
    tokens = store.load_tokens("a@b.c")
    assert tokens["access_token"] == "sekret" and "saved_at" in tokens
    assert store.load_tokens("missing@x.y") is None
    assert store.delete_account("missing@x.y") is False
    assert store.delete_account("a@b.c") is True
    assert store.list_accounts() == []


def test_wrong_key_yields_empty_store(monkeypatch, tmp_path):
    store = make_store(monkeypatch, tmp_path)
    store.save_tokens("a@b.c", {"access_token": "sekret"})
    monkeypatch.setattr(google_oauth, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    reopened = google_oauth.TokenStore(data_dir=str(tmp_path))
    assert reopened.list_accounts() == []
    assert reopened.load_tokens("a@b.c") is None


def test_invalid_key_raises_helpful_error(monkeypatch, tmp_path):
    monkeypatch.setattr(google_oauth, "TOKEN_ENCRYPTION_KEY", "not-a-key")
    try:
        google_oauth.TokenStore(data_dir=str(tmp_path))
    except RuntimeError as exc:
        assert "Fernet" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for invalid key")
