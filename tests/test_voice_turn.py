"""V18.2 voice-turn tests: path selection, wiring, streaming contract."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api-gateway'))
from schemas import TOOL_MODEL
from services import select_agent_model, select_model, select_voice_path

STREAM = 'stream'
AGENT = 'agent'


def test_conversation_goes_stream():
    assert select_voice_path('Tell me a quick joke.') == STREAM
    assert select_voice_path("What's 25 times 4?") == STREAM
    assert select_voice_path('How was your day?') == STREAM


def test_deep_conversation_streams_routed_model():
    # Deep content streams (Qwen via select_model); only tool intent uses agent.
    assert select_voice_path('Explain how a distributed database handles consensus and compare Raft with Paxos.') == STREAM
    assert select_voice_path('Write a Python script to rename files.') == STREAM


def test_tool_intent_goes_agent():
    for text in (
        'Turn on the living room TV.',
        'Search my Gmail for invoices.',
        'Check my calendar and tell me what I have coming up.',
        'Play some music.',
        'Remind me to call mom.',
    ):
        assert select_voice_path(text) == AGENT, text


def test_sales_profile_goes_agent():
    assert select_voice_path('Hello there.', 'sales') == AGENT


def test_explicit_model_still_honored_downstream():
    assert select_model('gemma3:4b', [{'role': 'user', 'content': 'x'}], 'general') == 'gemma3:4b'
    assert select_agent_model('auto', [{'role': 'user', 'content': 'x'}], 'general') == TOOL_MODEL


def test_system_prompt_does_not_change_voice_path():
    # select_voice_path only sees caller-supplied text; system prompts with
    # deep-hint words must not force the agent path.
    assert select_voice_path('Tell me a joke.') == STREAM


def test_voice_turn_endpoint_wiring():
    gateway = Path(__file__).resolve().parents[1] / 'api-gateway'
    src = (gateway / 'main.py').read_text(encoding='utf-8')
    assert '@app.post("/v1/voice/turn")' in src
    # V24 P0: request models live in schemas.py; main.py must import the one it wires.
    assert 'class VoiceTurnRequest' in (gateway / 'schemas.py').read_text(encoding='utf-8')
    assert 'VoiceTurnRequest' in src
    assert 'StreamingResponse(' in src
    assert 'X-Voice-Model' in src
    assert 'stream_chat(stream_request)' in src
    assert '@app.get("/voice-engine.js"' in src


def test_dashboard_uses_engine_and_streaming_turn():
    dash = (Path(__file__).resolve().parents[1] / 'api-gateway' / 'dashboard.html').read_text(encoding='utf-8')
    assert 'JarvisVoiceConsole' in dash
    assert 'openVoiceConsole' in dash
    assert '/voice-live.html' in dash
    assert 'LAUNCH LIVE DUPLEX MIC' in dash


def test_voice_console_uses_engine_and_turn():
    page = (Path(__file__).resolve().parents[1] / 'api-gateway' / 'voice.html').read_text(encoding='utf-8')
    assert '<script src="/voice-engine.js"></script>' in page
    assert '/v1/voice/turn' in page
    assert 'createSentenceBuffer' in page
    assert 'localTurnReady' in page
    assert 'shouldBarge' in page
    assert 'selectProvider' in page
    assert 'createSubmitGuard' in page
    # No full-response wait: streaming reader and incremental queue present.
    assert 'getReader()' in page
    assert 'text/event-stream' in page
    assert '/v1/voice/turn' in page
    assert 'X-Voice-Model' in (Path(__file__).resolve().parents[1] / 'api-gateway' / 'main.py').read_text(encoding='utf-8')
    assert 'speechFrames' in (Path(__file__).resolve().parents[1] / 'api-gateway' / 'voice-engine.js').read_text(encoding='utf-8')
