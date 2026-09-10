import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api-gateway'))
from orchestrator import OrchestratorStore, build_plan
from security import requires_confirmation, risk


def test_plan_is_deterministic_and_conservative():
    p = build_plan('Build and test a Python API')
    assert p['routing']['complexity'] == 'deep'
    assert [x['kind'] for x in p['tasks']][-1] == 'verify'


def test_tool_goal_includes_execution():
    p = build_plan('Check Gmail and send an invoice follow-up')
    assert p['routing']['execution'] == 'tools'
    assert any(t['kind'] == 'execute' for t in p['tasks'])


def test_project_task_lifecycle(tmp_path):
    s = OrchestratorStore(tmp_path / 'jarvis.db')
    p = s.create_project('Explain how my system works')
    assert p['status'] == 'active'
    t = s.next_task(p['id'])
    assert t and t['status'] == 'ready'
    p2 = s.transition(t['id'], 'completed', {'ok': True})
    assert p2['tasks'][1]['status'] == 'ready'
    assert s.audit(p['id'])


def test_security_defaults():
    assert risk('read_file') == 'read'
    assert not requires_confirmation('read_file')
    assert requires_confirmation('computer_shell')
    assert risk('something_unknown') == 'unknown'
