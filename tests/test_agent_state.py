import ast
from pathlib import Path

p=Path(__file__).parents[1]/"api-gateway"/"services.py"
ast.parse(p.read_text(encoding="utf-8"))
s=p.read_text(encoding="utf-8")
for needle in ["_save_confirmation_state", "_run_agent_loop", "resume", "assistant_profile"]:
    assert needle in s
print("AGENT-STATE-OK")
