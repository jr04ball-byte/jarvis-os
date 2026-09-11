from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_voice_prompt_is_voice_to_voice_aware():
    text = (ROOT / "api-gateway" / "services.py").read_text(encoding="utf-8")
    assert 'Voice-to-voice is a first-class interaction mode' in text
    assert 'Never tell the user that you cannot access their microphone' in text
    assert 'version=APP_VERSION' in (ROOT / "api-gateway" / "main.py").read_text(encoding="utf-8")
    assert 'APP_VERSION = "24.0.0-rc1"' in (ROOT / "api-gateway" / "deps.py").read_text(encoding="utf-8")


def test_dashboard_has_barge_in_and_local_voice():
    text = (ROOT / "api-gateway" / "dashboard.html").read_text(encoding="utf-8")
    assert 'JarvisVoiceConsole' in text
    assert 'openVoiceConsole' in text
    assert '/voice-live.html' in text
