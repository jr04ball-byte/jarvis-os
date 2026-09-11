"""Regression tests for confirmation/resume state in the agent service layer."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
API = ROOT / "api-gateway"
sys.path.insert(0, str(API))


def test_agent_service_module_parses_and_keeps_resume_contract():
    source = (API / "services.py").read_text(encoding="utf-8")
    ast.parse(source)
    for needle in ("_save_confirmation_state", "_run_agent_loop", "resume", "assistant_profile"):
        assert needle in source
