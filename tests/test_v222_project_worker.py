import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api-gateway'))

from project_worker import (
    classify_verification_failure,
    project_health,
    select_verification_commands,
)


def test_targeted_javascript_test_selection(tmp_path):
    package = {
        'name': 'demo',
        'scripts': {'test': 'vitest run'},
        'devDependencies': {'vitest': '^2.0.0'},
    }
    (tmp_path / 'package.json').write_text(json.dumps(package))
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'thing.test.ts').write_text('test("x", () => {})\n')
    cmds = select_verification_commands(str(tmp_path), ['src/thing.test.ts'], run_build=False)
    assert cmds
    assert cmds[0][:3] == ['npm', 'test', '--']
    assert 'src/thing.test.ts' in cmds[0]


def test_environment_failure_classification_missing_command():
    verification = {
        'ok': False,
        'status': 'failed',
        'commands_run': [
            {'ok': False, 'exit_code': 127, 'stdout': '', 'stderr': 'npm: command not found'}
        ],
    }
    result = classify_verification_failure(verification)
    assert result['kind'] == 'environment'
    assert result['confidence'] >= 0.9


def test_code_failure_classification_assertion():
    verification = {
        'ok': False,
        'status': 'failed',
        'commands_run': [
            {'ok': False, 'exit_code': 1, 'stdout': 'FAILED tests/test_x.py::test_x - AssertionError', 'stderr': ''}
        ],
    }
    result = classify_verification_failure(verification)
    assert result['kind'] == 'code'


def test_project_health_reports_repo_and_commands(tmp_path):
    subprocess.run(['git', 'init'], cwd=tmp_path, capture_output=True)
    (tmp_path / 'pytest.ini').write_text('[pytest]\n')
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests' / 'test_ok.py').write_text('def test_ok(): assert True\n')
    health = project_health(str(tmp_path))
    assert 'python' in health['ecosystems'] or health['commands']['tests']
    assert health['git']['available'] is True
    assert health['commands']['tests']
