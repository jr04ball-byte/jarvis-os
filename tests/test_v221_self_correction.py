import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api-gateway'))

from project_worker import (
    WorkerRunStore,
    capture_workspace_baseline,
    compare_workspace_baseline,
    make_repair_prompt,
    select_verification_commands,
)


def test_baseline_detects_worker_delta_and_preexisting(tmp_path):
    subprocess.run(['git', 'init'], cwd=tmp_path, capture_output=True)
    (tmp_path / 'a.py').write_text('x = 1\n')
    (tmp_path / 'user.txt').write_text('before\n')
    subprocess.run(['git', 'add', '.'], cwd=tmp_path, capture_output=True)
    subprocess.run(['git', '-c', 'user.name=Jarvis', '-c', 'user.email=jarvis@example.com', 'commit', '-m', 'init'], cwd=tmp_path, capture_output=True)
    (tmp_path / 'user.txt').write_text('user change\n')
    baseline = capture_workspace_baseline(str(tmp_path))
    (tmp_path / 'a.py').write_text('x = 2\n')
    delta = compare_workspace_baseline(str(tmp_path), baseline)
    assert 'a.py' in delta['changed_by_worker']
    assert 'user.txt' in delta['preexisting_git_changes']
    assert 'user.txt' not in delta['changed_by_worker']


def test_targeted_python_test_selection(tmp_path):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'foo.py').write_text('x=1\n')
    (tmp_path / 'tests' / 'test_foo.py').write_text('def test_x(): assert True\n')
    cmds = select_verification_commands(str(tmp_path), ['foo.py'], run_build=False)
    assert cmds
    assert cmds[0][:4] == ['python', '-m', 'pytest', '-q']
    assert 'tests/test_foo.py' in cmds[0]


def test_worker_run_store_is_resumable(tmp_path):
    store = WorkerRunStore(str(tmp_path / 'runs.db'))
    run = store.create('email_agent', 'C:/repo', 'Fix issue', {'git_paths': []})
    store.update(run['id'], status='repairing', attempt={'attempt': 1, 'ok': False})
    loaded = store.get(run['id'])
    assert loaded['status'] == 'repairing'
    assert loaded['attempts'][0]['attempt'] == 1


def test_repair_prompt_contains_failure_evidence(tmp_path):
    verification = {'commands_run': [{'ok': False, 'command': ['pytest'], 'exit_code': 1, 'stdout': 'FAIL test_x', 'stderr': ''}]}
    delta = {'changed_by_worker': ['foo.py'], 'preexisting_git_changes': ['notes.txt']}
    prompt = make_repair_prompt('Fix it', str(tmp_path), verification, delta, 2)
    assert 'FAIL test_x' in prompt
    assert 'foo.py' in prompt
    assert 'notes.txt' in prompt
