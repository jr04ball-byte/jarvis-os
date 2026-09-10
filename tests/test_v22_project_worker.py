import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api-gateway'))

from project_worker import (
    discover_commands,
    inspect_workspace,
    make_worker_prompt,
    verify_workspace,
)


def test_discovers_python_tests(tmp_path):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests' / 'test_x.py').write_text('def test_x(): assert True\n')
    commands = discover_commands(tmp_path)
    assert ['python', '-m', 'pytest', '-q'] in commands['tests']


def test_discovers_package_scripts(tmp_path):
    (tmp_path / 'package.json').write_text(json.dumps({'scripts': {'test':'vitest run','build':'vite build','lint':'eslint .'}}))
    commands = discover_commands(tmp_path)
    assert any(c[0] == 'npm' and 'test' in c for c in commands['tests'])
    assert ['npm', 'run', 'build'] in commands['builds']
    assert ['npm', 'run', 'lint'] in commands['lint']


def test_pnpm_lint_uses_lint_script_not_stale_loop_var(tmp_path):
    (tmp_path / 'package.json').write_text(json.dumps({'scripts': {'lint': 'eslint .'}}))
    (tmp_path / 'pnpm-lock.yaml').write_text('lockfileVersion: 9\n')
    commands = discover_commands(tmp_path)
    assert commands['lint'] == [['pnpm', 'lint']]


def test_inspection_does_not_read_env(tmp_path):
    (tmp_path / '.env').write_text('TOP_SECRET=abc')
    (tmp_path / 'README.md').write_text('hello')
    subprocess.run(['git','init'], cwd=tmp_path, capture_output=True)
    snap = inspect_workspace(str(tmp_path))
    assert '.env' not in snap['files']
    assert snap['files']['README.md'] == 'hello'


def test_verifier_runs_pytest_and_collects_git(tmp_path):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests' / 'test_ok.py').write_text('def test_ok(): assert 2 + 2 == 4\n')
    subprocess.run(['git','init'], cwd=tmp_path, capture_output=True)
    result = verify_workspace(str(tmp_path), run_build=False)
    assert result['status'] == 'verified'
    assert result['commands_run'][0]['exit_code'] == 0


def test_worker_prompt_enforces_boundaries(tmp_path):
    prompt = make_worker_prompt('Fix the bug', str(tmp_path), {'workspace':str(tmp_path),'commands':{},'git':{},'blockers':[]})
    assert 'Never read, print, change, or expose .env' in prompt
    assert 'Preserve unrelated user changes' in prompt
