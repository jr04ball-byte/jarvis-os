from pathlib import Path
import ast, re, zipfile, tempfile

root = Path(__file__).parent
main = root / "api-gateway" / "main.py"
voice = root / "api-gateway" / "voice.html"
compose = root / "docker-compose.yml"

ast.parse(main.read_text(encoding="utf-8"))
html = voice.read_text(encoding="utf-8")
script = re.search(r'<script type="module">(.*?)</script>', html, re.S).group(1)
Path(tempfile.gettempdir(), 'ai-system-voice-check.js').write_text(script, encoding='utf-8')

assert "language:'english'" not in script
assert "task:'transcribe'" not in script
assert "assistant_profile:$('profile').value" in script or "assistant_profile:$('profile').value" in script
assert "j.conversation_id" in script
assert "wss://api.deepgram.com/v2/listen" in script
assert "flux-general-en" in script
assert "/v1/deepgram-token" in script
assert "/v1/deepgram-speak" in script
assert "new WebSocket" in script and "['token',t]" in script
assert "MediaRecorder" not in script

source = main.read_text(encoding="utf-8")
assert 'CORE_SYSTEM_PROMPT' in source
assert 'SALES_SYSTEM_PROMPT' in source
assert '@app.post("/v1/sales/chat")' in source
assert 'Email Agent SaaS' in source
assert 'assistant_profile' in source
assert 'confirmation_id' in source
assert '_consume_confirmation' in source
assert 'system messages are not accepted by the agent endpoint' in source
assert 'connected Google identity' in source

compose_text = compose.read_text(encoding="utf-8")
assert '127.0.0.1:8000:8000' in compose_text
assert '127.0.0.1:3000:8080' in compose_text
assert '127.0.0.1:11434:11434' in compose_text

for bad in ['GOCSPX-', 'BEGIN PRIVATE KEY']:
    assert bad not in source + html + compose_text

print('AST-OK')
print('VOICE-OK')
print('SALES-SEPARATION-OK')
print('COMPOSE-LOCALHOST-OK')
assert 'AI_HOST_FILES is not configured' in (root / 'host-bridge' / 'host_bridge.py').read_text(encoding='utf-8')
assert 'path outside configured host files' in (root / 'host-bridge' / 'host_bridge.py').read_text(encoding='utf-8')
print('SECRET-SCAN-OK')
print('TOOL-ROUTER-HARDENED-OK')

main_source = source
assert 'if req.tool.startswith("gmail_") or req.tool.startswith("calendar_"):' in main_source
assert 'if req.tool=="home_states": return {"result":await tools.ha_states()}' in main_source
assert 'email=a["email"]' not in main_source
voice_source = html
assert 'function headers(json=false)' in voice_source
assert 'function pcm(a)' in voice_source
assert '/v1/deepgram-token' in voice_source
assert '/v1/deepgram-speak' in voice_source
assert 'setTimeout(r,50)' not in voice_source
host_source = (root / 'host-bridge' / 'host_bridge.py').read_text(encoding='utf-8')
assert "AI_HOST_BRIDGE_BIND', '127.0.0.1'" in host_source
assert "only /host-files paths are allowed" in host_source
ios_source = (root / 'ios-companion' / 'AICompanion.swift').read_text(encoding='utf-8')
assert 'let messages: [ChatMessage]' in ios_source
assert 'conversation_id: Int?' in ios_source
assert 'SecureField("API token (optional)"' in ios_source
assert 'AI_HOST_BRIDGE_BIND' in (root / '.env.example').read_text(encoding='utf-8')
assert 'version="14.0.0"' in main_source
# Stable + fast local run guarantees (V15-FIXED perfect baseline)
assert main_source.count('"think": False') >= 3, 'Ollama calls must disable thinking'
assert 'OLLAMA_MODEL", "qwen3.5:9b"' in main_source, 'default model must be qwen3.5:9b'
assert '@app.get("/health")' in main_source
for bat in ['START-LOCAL-TEST.bat', 'OPEN-JARVIS.bat']:
    txt = (root / bat).read_text(encoding='utf-8')
    assert 'set OLLAMA_MODEL=qwen3.5:9b' in txt, f'{bat} must pin OLLAMA_MODEL'
    assert 'StatusCode -eq 200' in txt, f'{bat} must wait for HTTP 200 health'
    assert 'pip.ok' in txt, f'{bat} must have fast pip path'
print('STABLE-FAST-OK')
dash = (root / 'api-gateway' / 'dashboard.html').read_text(encoding='utf-8')
for need in ['data-view="homeai"', 'data-view="sales"', 'data-view="approvals"', 'toggleTTS()', 'stopSpeaking()', 'testTTS()', 'loadApprovals()', '/v1/sales/chat', '/v1/agent/confirm', 'detectVoice()', 'approvalBadge', 'ttsStatus']:
    assert need in dash, f'dashboard missing {need}'
assert 'data-target=' not in dash, 'old data-target nav must be gone'
print('DASHBOARD-TABS-TTS-OK')
assert 'orbCanvas' in dash and 'orbEngine()' in dash, 'live orb missing'
assert 'orb-loop.webm' in dash, 'orb video slot missing'
assert '/orb-loop.webm' in main_source, 'orb asset route missing'
import sys as _s
_s.path.insert(0, str(root / 'api-gateway'))
import local_tools as _lt
names = {t['name']: t for t in _lt.scan_local_tools()['tools']}
assert names['Blender']['installed'], 'Blender 5.2 must be detected'
assert names['Unreal Engine 5']['installed'], 'UE 5.8 must be detected'
assert (root / 'tools' / 'blender_orb_bake.py').exists(), 'blender bake script missing'
assert 'micSens' in dash and 'calibrateMic()' in dash and 'noiseFloor' in dash, 'mic calibration missing'
assert 'bgVideo' in dash and '/bg-loop.webm' in dash, 'bg loop slot missing'
assert '/bg-loop.webm' in main_source, 'bg asset route missing'
assert (root / 'tools' / 'blender_bg_bake.py').exists(), 'bg bake script missing'
print('VISUALS-ORB-OK')
