from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def test_v25_voice_policy():
    text = (ROOT / 'api-gateway/services.py').read_text(encoding='utf-8')
    assert 'push-to-talk transcription and returns text only' in text
    assert 'Voice-to-voice is a first-class' not in text

def test_dashboard_launches_ptt():
    text = (ROOT / 'api-gateway/dashboard.html').read_text(encoding='utf-8')
    assert 'openVoiceConsole' in text
    assert '/voice-live.html' not in text
    assert 'Push to talk' in text
