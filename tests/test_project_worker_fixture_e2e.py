"""Deterministic Project Worker fixture: inspect -> fail -> repair -> verify -> evidence."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

from project_worker import (
    capture_workspace_baseline,
    compare_workspace_baseline,
    inspect_workspace,
    verify_workspace,
)

FIXTURE = Path(__file__).parent / "fixtures" / "project_worker_repo"


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "jarvis-fixture@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Jarvis Fixture"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture baseline"], cwd=root, check=True)


def test_project_worker_fixture_full_repair_cycle(tmp_path):
    repo = tmp_path / "fixture-repo"
    shutil.copytree(FIXTURE, repo)
    _init_git_repo(repo)

    inspection = inspect_workspace(str(repo))
    assert inspection["workspace"] == str(repo.resolve())
    assert inspection["git"]["available"] is True
    assert inspection["commands"]["tests"]

    baseline = capture_workspace_baseline(str(repo))
    failed = verify_workspace(str(repo), run_build=False, run_lint=False)
    assert failed["ok"] is False
    assert failed["status"] == "failed"
    assert failed["failure_classification"]["kind"] == "code"

    # Deterministic stand-in for the bounded repair worker: make the exact
    # minimal source correction implied by the failing assertion.
    (repo / "calculator.py").write_text(
        '"""Repaired fixture implementation."""\n\n'
        'def add(a: int, b: int) -> int:\n'
        '    return a + b\n',
        encoding="utf-8",
    )

    delta = compare_workspace_baseline(str(repo), baseline)
    assert "calculator.py" in delta["changed_by_worker"]

    passed = verify_workspace(
        str(repo),
        run_build=False,
        run_lint=False,
        changed_paths=delta["changed_by_worker"],
    )
    assert passed["ok"] is True
    assert passed["status"] == "verified"
    assert passed["git"]["available"] is True
