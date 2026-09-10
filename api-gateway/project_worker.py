from __future__ import annotations

import builtins
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

SENSITIVE_NAMES = {'.env', '.env.local', '.env.production', '.env.development', 'id_rsa', 'id_ed25519'}
SENSITIVE_SUFFIXES = {'.pem', '.p12', '.pfx', '.key'}
SKIP_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', '.next', 'dist', 'build', 'coverage', '.pytest_cache'}
READ_CANDIDATES = [
    'README.md', 'README.txt', 'package.json', 'pyproject.toml', 'requirements.txt',
    'pytest.ini', 'tox.ini', 'setup.cfg', 'tsconfig.json', 'next.config.js',
    'next.config.mjs', 'vite.config.js', 'vite.config.ts', 'docker-compose.yml',
    'docker-compose.yaml', 'Dockerfile',
]


def _package_metadata(root: Path) -> dict[str, Any]:
    package = root / 'package.json'
    if not package.exists():
        return {}
    try:
        data = json.loads(package.read_text(encoding='utf-8'))
        scripts = data.get('scripts') or {}
        runner = 'npm'
        if (root / 'pnpm-lock.yaml').exists():
            runner = 'pnpm'
        elif (root / 'yarn.lock').exists():
            runner = 'yarn'
        elif (root / 'bun.lockb').exists() or (root / 'bun.lock').exists():
            runner = 'bun'
        deps = {}
        deps.update(data.get('dependencies') or {})
        deps.update(data.get('devDependencies') or {})
        test_script = str(scripts.get('test') or '')
        framework = None
        lower = test_script.lower() + ' ' + ' '.join(str(k).lower() for k in deps)
        for candidate in ('vitest', 'jest', 'mocha'):
            if candidate in lower:
                framework = candidate
                break
        if framework is None and 'node --test' in test_script.lower():
            framework = 'node-test'
        return {'runner': runner, 'scripts': scripts, 'framework': framework, 'name': data.get('name')}
    except Exception:
        return {}


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def project_health(workspace: str) -> dict[str, Any]:
    """Read-only readiness evidence gathered before a worker is allowed to edit files."""
    root = _safe_resolve(workspace)
    commands = discover_commands(root)
    package = _package_metadata(root)
    ecosystems: list[str] = []
    if package:
        ecosystems.append('javascript')
    if (root / 'pyproject.toml').exists() or (root / 'requirements.txt').exists() or (root / 'pytest.ini').exists():
        ecosystems.append('python')
    if (root / 'Dockerfile').exists() or (root / 'docker-compose.yml').exists() or (root / 'docker-compose.yaml').exists():
        ecosystems.append('docker')

    required_tools = {'git'}
    for group in commands.values():
        for cmd in group:
            if cmd:
                required_tools.add(cmd[0])
    tools = {name: _tool_available(name) for name in sorted(required_tools)}
    blockers = []
    if not tools.get('git', False):
        blockers.append('git executable is unavailable')
    for name, available in tools.items():
        if name != 'git' and not available:
            blockers.append(f'required command is unavailable: {name}')
    if not commands.get('tests'):
        blockers.append('no test command discovered')

    git_status = run_command(['git', 'status', '--short', '--branch'], str(root), 20)
    git_head = run_command(['git', 'rev-parse', 'HEAD'], str(root), 20)
    git_branch = run_command(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], str(root), 20)
    return {
        'workspace': str(root),
        'ecosystems': ecosystems,
        'package': {'runner': package.get('runner'), 'framework': package.get('framework'), 'name': package.get('name')} if package else {},
        'commands': commands,
        'tools': tools,
        'git': {
            'available': git_status.exit_code == 0,
            'head': (git_head.stdout or '').strip(),
            'branch': (git_branch.stdout or '').strip() if git_branch.exit_code == 0 else '',
            'changed_paths': _git_changed_paths(root),
        },
        'blockers': blockers,
        'ready_for_worker': not any(x.startswith('required command is unavailable') or x.startswith('git executable') for x in blockers),
    }


def classify_verification_failure(verification: dict[str, Any]) -> dict[str, Any]:
    """Classify failed verification so Jarvis does not ask a coding agent to repair the machine/network."""
    if verification.get('ok'):
        return {'kind': 'none', 'confidence': 1.0, 'reasons': []}
    if verification.get('status') == 'no_checks':
        return {'kind': 'environment', 'confidence': 0.9, 'reasons': ['no verification checks were available']}

    env_patterns = [
        r'command not found', r'not recognized as an internal or external command', r'no such file or directory',
        r'modulenotfounderror', r'cannot find module', r'enoent', r'eacces', r'permission denied',
        r'enotfound', r'econnreset', r'etimedout', r'network.*(unreachable|error)', r'could not resolve host',
        r'failed to fetch', r'unable to download', r'no module named pytest', r'no module named ruff', r'no module named mypy',
    ]
    code_patterns = [
        r'assertionerror', r'failed\s+tests?', r'\bsyntaxerror\b', r'\btypeerror\b', r'\breferenceerror\b',
        r'tests? failed', r'compilation failed', r'build failed', r'error ts\d+', r'pytest.*failed',
    ]
    env_hits: list[str] = []
    code_hits: list[str] = []
    for item in verification.get('commands_run') or []:
        if item.get('ok'):
            continue
        text = f"{item.get('stderr') or ''}\n{item.get('stdout') or ''}".lower()
        if item.get('exit_code') == 127:
            env_hits.append('command executable not found')
        for pat in env_patterns:
            if re.search(pat, text, re.I):
                env_hits.append(pat)
        for pat in code_patterns:
            if re.search(pat, text, re.I):
                code_hits.append(pat)
    if env_hits and not code_hits:
        return {'kind': 'environment', 'confidence': 0.95, 'reasons': sorted(set(env_hits))[:8]}
    if code_hits and not env_hits:
        return {'kind': 'code', 'confidence': 0.9, 'reasons': sorted(set(code_hits))[:8]}
    if env_hits and code_hits:
        return {'kind': 'mixed', 'confidence': 0.65, 'reasons': sorted(set(env_hits + code_hits))[:8]}
    return {'kind': 'code', 'confidence': 0.55, 'reasons': ['verification command failed without a recognized environment signature']}

@dataclass
class CommandEvidence:
    command: list[str]
    cwd: str
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    elapsed_ms: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tail(text: str, limit: int = 12000) -> str:
    text = text or ''
    return text[-limit:]


def _safe_resolve(workspace: str) -> Path:
    p = Path(workspace).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f'workspace not found: {workspace}')
    return p


def _safe_file(path: Path) -> bool:
    name = path.name.lower()
    return name not in SENSITIVE_NAMES and path.suffix.lower() not in SENSITIVE_SUFFIXES


def run_command(command: list[str], workspace: str, timeout: int = 120) -> CommandEvidence:
    root = _safe_resolve(workspace)
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=max(1, min(timeout, 900)),
            shell=False,
            env=os.environ.copy(),
        )
        return CommandEvidence(command, str(root), proc.returncode == 0, proc.returncode,
                               _tail(proc.stdout), _tail(proc.stderr), int((time.time()-started)*1000))
    except FileNotFoundError as exc:
        return CommandEvidence(command, str(root), False, 127, '', str(exc), int((time.time()-started)*1000))
    except subprocess.TimeoutExpired as exc:
        return CommandEvidence(command, str(root), False, 124,
                               _tail(exc.stdout or ''), _tail(exc.stderr or 'command timed out'),
                               int((time.time()-started)*1000))


def _read_text(path: Path, max_chars: int = 30000) -> str | None:
    if not path.is_file() or not _safe_file(path):
        return None
    try:
        if path.stat().st_size > 512_000:
            return None
        return path.read_text(encoding='utf-8', errors='replace')[:max_chars]
    except Exception:
        return None


def _top_level(root: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(root.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:200]:
        if p.name in SKIP_DIRS:
            continue
        out.append({'name': p.name, 'type': 'dir' if p.is_dir() else 'file'})
    return out


def discover_commands(root: Path) -> dict[str, list[list[str]]]:
    tests: list[list[str]] = []
    builds: list[list[str]] = []
    lint: list[list[str]] = []

    package = root / 'package.json'
    if package.exists():
        try:
            data = json.loads(package.read_text(encoding='utf-8'))
            scripts = data.get('scripts') or {}
            runner = 'npm'
            if (root / 'pnpm-lock.yaml').exists():
                runner = 'pnpm'
            elif (root / 'yarn.lock').exists():
                runner = 'yarn'
            elif (root / 'bun.lockb').exists() or (root / 'bun.lock').exists():
                runner = 'bun'
            if 'test' in scripts:
                tests.append([runner, 'test'])
            for name in ('test:unit', 'test:ci', 'test:integration'):
                if name in scripts:
                    tests.append([runner, 'run', name] if runner == 'npm' else [runner, name])
            if 'build' in scripts:
                builds.append([runner, 'run', 'build'] if runner == 'npm' else [runner, 'build'])
            if 'lint' in scripts:
                lint.append([runner, 'run', 'lint'] if runner == 'npm' else [runner, 'lint'])
        except Exception as exc:
            logger.debug("package.json inspection failed for %s: %s", root, exc)

    if (root / 'pytest.ini').exists() or (root / 'pyproject.toml').exists() or any(root.glob('test*.py')) or (root / 'tests').is_dir():
        tests.append(['python', '-m', 'pytest', '-q'])
    if (root / 'pyproject.toml').exists():
        text = _read_text(root / 'pyproject.toml', 20000) or ''
        if '[tool.ruff' in text or 'ruff' in text:
            lint.append(['python', '-m', 'ruff', 'check', '.'])
        if 'mypy' in text:
            lint.append(['python', '-m', 'mypy', '.'])

    def uniq(items: list[list[str]]) -> list[list[str]]:
        seen = set(); out=[]
        for x in items:
            k=tuple(x)
            if k not in seen: seen.add(k); out.append(x)
        return out
    return {'tests': uniq(tests), 'builds': uniq(builds), 'lint': uniq(lint)}


def _git_changed_paths(root: Path) -> list[str]:
    ev = run_command(['git', 'status', '--porcelain=v1', '-z'], str(root), 20)
    if ev.exit_code != 0:
        return []
    parts = ev.stdout.split('\x00')
    paths: list[str] = []
    for part in parts:
        if not part or len(part) < 4:
            continue
        raw = part[3:]
        if ' -> ' in raw:
            raw = raw.split(' -> ', 1)[1]
        paths.append(raw.replace('\\', '/'))
    return sorted(set(paths))


def _iter_workspace_files(root: Path, max_files: int = 20000) -> Iterable[Path]:
    count = 0
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        b = Path(base)
        for name in files:
            p = b / name
            if not _safe_file(p):
                continue
            yield p
            count += 1
            if count >= max_files:
                return


def _fingerprint(path: Path) -> str | None:
    try:
        if path.stat().st_size > 2_000_000:
            return f'size:{path.stat().st_size}:mtime:{path.stat().st_mtime_ns}'
        h = hashlib.sha256()
        with path.open('rb') as fh:
            for chunk in iter(lambda: fh.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def capture_workspace_baseline(workspace: str) -> dict[str, Any]:
    """Capture enough state to distinguish worker edits from pre-existing user changes."""
    root = _safe_resolve(workspace)
    fingerprints: dict[str, str] = {}
    for p in _iter_workspace_files(root):
        fp = _fingerprint(p)
        if fp:
            fingerprints[p.relative_to(root).as_posix()] = fp
    return {
        'workspace': str(root),
        'captured_at': int(time.time()),
        'git_paths': _git_changed_paths(root),
        'fingerprints': fingerprints,
        'head': (run_command(['git', 'rev-parse', 'HEAD'], str(root), 20).stdout or '').strip(),
    }


def compare_workspace_baseline(workspace: str, baseline: dict[str, Any]) -> dict[str, Any]:
    root = _safe_resolve(workspace)
    before = baseline.get('fingerprints') or {}
    after: dict[str, str] = {}
    for p in _iter_workspace_files(root):
        fp = _fingerprint(p)
        if fp:
            after[p.relative_to(root).as_posix()] = fp
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    preexisting = sorted(set(baseline.get('git_paths') or []))
    return {
        'changed_by_worker': changed,
        'preexisting_git_changes': preexisting,
        'overlap_with_preexisting': sorted(set(changed) & set(preexisting)),
        'new_git_paths': sorted(set(_git_changed_paths(root)) - set(preexisting)),
        'head_changed': (run_command(['git', 'rev-parse', 'HEAD'], str(root), 20).stdout or '').strip() != (baseline.get('head') or ''),
    }


def inspect_workspace(workspace: str) -> dict[str, Any]:
    root = _safe_resolve(workspace)
    evidence: dict[str, Any] = {
        'workspace': str(root),
        'top_level': _top_level(root),
        'files': {},
        'commands': discover_commands(root),
        'git': {},
        'blockers': [],
    }
    for rel in READ_CANDIDATES:
        p = root / rel
        if p.is_file():
            text = _read_text(p)
            if text is not None:
                evidence['files'][rel] = text

    git = run_command(['git', 'status', '--short', '--branch'], str(root), 20)
    if git.exit_code == 0:
        evidence['git']['status'] = git.as_dict()
        evidence['git']['changed_paths'] = _git_changed_paths(root)
        branch = run_command(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], str(root), 20)
        evidence['git']['branch'] = (branch.stdout or '').strip()
        head = run_command(['git', 'rev-parse', 'HEAD'], str(root), 20)
        evidence['git']['head'] = (head.stdout or '').strip()
    else:
        evidence['git']['available'] = False
        evidence['blockers'].append('workspace is not a readable git repository or git is unavailable')

    if not evidence['commands']['tests']:
        evidence['blockers'].append('no test command discovered')
    return evidence


def capture_git_diff(workspace: str) -> dict[str, Any]:
    root = _safe_resolve(workspace)
    status = run_command(['git', 'status', '--short'], str(root), 20)
    diff = run_command(['git', 'diff', '--no-ext-diff', '--'], str(root), 30)
    staged = run_command(['git', 'diff', '--cached', '--no-ext-diff', '--'], str(root), 30)
    return {'status': status.as_dict(), 'diff': diff.as_dict(), 'staged_diff': staged.as_dict()}


def select_verification_commands(workspace: str, changed_paths: list[str] | None = None,
                                 *, run_tests: bool = True, run_build: bool = True,
                                 run_lint: bool = False, max_commands: int = 4) -> list[list[str]]:
    root = _safe_resolve(workspace)
    discovered = discover_commands(root)
    selected: list[list[str]] = []
    changed_paths = changed_paths or []

    if run_tests:
        package = _package_metadata(root)
        targeted_js: list[str] = []
        if package and package.get('scripts', {}).get('test'):
            for rel in changed_paths:
                low = rel.lower().replace('\\', '/')
                if low.endswith(('.test.js', '.test.jsx', '.test.ts', '.test.tsx', '.spec.js', '.spec.jsx', '.spec.ts', '.spec.tsx')) or '/__tests__/' in f'/{low}':
                    if (root / rel).exists():
                        targeted_js.append(rel.replace('\\', '/'))
            targeted_js = sorted(set(targeted_js))
            if targeted_js:
                runner = package.get('runner') or 'npm'
                if runner == 'npm':
                    selected.append(['npm', 'test', '--', *targeted_js[:8]])
                elif runner == 'pnpm':
                    selected.append(['pnpm', 'test', *targeted_js[:8]])
                elif runner == 'yarn':
                    selected.append(['yarn', 'test', *targeted_js[:8]])
                elif runner == 'bun':
                    selected.append(['bun', 'test', *targeted_js[:8]])

        targeted_py: list[str] = []
        for rel in changed_paths:
            p = Path(rel)
            if rel.startswith('tests/') and p.suffix == '.py' and (root / rel).exists():
                targeted_py.append(rel)
                continue
            if p.suffix == '.py' and not rel.startswith('tests/'):
                stem = p.stem
                candidates = [root / 'tests' / f'test_{stem}.py', root / f'test_{stem}.py']
                for c in candidates:
                    if c.exists():
                        targeted_py.append(c.relative_to(root).as_posix())
        targeted_py = sorted(set(targeted_py))
        if targeted_py:
            selected.append(['python', '-m', 'pytest', '-q', *targeted_py[:8]])
        elif not targeted_js and discovered['tests']:
            selected.append(discovered['tests'][0])

    if run_build and discovered['builds']:
        selected.append(discovered['builds'][0])
    if run_lint and discovered['lint']:
        selected.append(discovered['lint'][0])

    out: list[list[str]] = []
    seen = set()
    for cmd in selected:
        k = tuple(cmd)
        if k not in seen:
            seen.add(k); out.append(cmd)
    return out[:max_commands]


def verify_workspace(workspace: str, *, run_tests: bool = True, run_build: bool = True, run_lint: bool = False,
                     max_commands: int = 4, changed_paths: list[str] | None = None) -> dict[str, Any]:
    root = _safe_resolve(workspace)
    discovered = discover_commands(root)
    selected = select_verification_commands(str(root), changed_paths, run_tests=run_tests, run_build=run_build,
                                            run_lint=run_lint, max_commands=max_commands)
    results = [run_command(cmd, str(root), 300).as_dict() for cmd in selected]
    ok = bool(selected) and all(r['ok'] for r in results)
    payload = {
        'workspace': str(root), 'commands_discovered': discovered, 'commands_selected': selected,
        'commands_run': results, 'changed_paths': changed_paths or [],
        'ok': ok, 'status': 'verified' if ok else ('no_checks' if not selected else 'failed'),
        'git': capture_git_diff(str(root)),
    }
    payload['failure_classification'] = classify_verification_failure(payload)
    return payload


def make_worker_prompt(goal: str, workspace: str, inspection: dict[str, Any], *, permission: str = 'workspace_write',
                       baseline: dict[str, Any] | None = None) -> str:
    compact = {
        'workspace': inspection.get('workspace'),
        'git': inspection.get('git'),
        'commands': inspection.get('commands'),
        'blockers': inspection.get('blockers'),
        'top_level': inspection.get('top_level', [])[:80],
        'config_files': list((inspection.get('files') or {}).keys()),
        'preexisting_changes': (baseline or {}).get('git_paths', []),
    }
    return f'''You are Jarvis Project Worker. Implement the requested goal in the target repository.

GOAL:\n{goal}

REPOSITORY EVIDENCE:\n{json.dumps(compact, indent=2, default=str)}

BOUNDARIES:
- Work only inside: {workspace}
- Permission: {permission}
- Never read, print, change, or expose .env files, secrets, credentials, private keys, or tokens.
- Do not delete repositories, reset git history, force-push, deploy, publish, purchase, send messages, or perform other external side effects.
- Preserve unrelated user changes. Pre-existing changed paths are listed above; do not overwrite or revert them unless the goal specifically requires editing the same file.
- Do not use destructive git commands or create commits.
- Make the smallest coherent change that satisfies the goal.
- Add/update tests when appropriate.
- Prefer targeted tests before full-suite checks.
- Return a concise summary of files changed, commands run, results, and remaining blockers.
'''


def make_repair_prompt(goal: str, workspace: str, verification: dict[str, Any], delta: dict[str, Any], attempt: int) -> str:
    failures = []
    for item in verification.get('commands_run') or []:
        if not item.get('ok'):
            failures.append({
                'command': item.get('command'), 'exit_code': item.get('exit_code'),
                'stdout': _tail(item.get('stdout') or '', 5000), 'stderr': _tail(item.get('stderr') or '', 5000),
            })
    payload = {
        'attempt': attempt,
        'changed_by_worker': delta.get('changed_by_worker', []),
        'preexisting_changes': delta.get('preexisting_git_changes', []),
        'failures': failures,
    }
    return f'''You are Jarvis Project Worker's repair cycle. The prior implementation did not verify.

ORIGINAL GOAL:\n{goal}
WORKSPACE: {workspace}
FAILURE EVIDENCE:\n{json.dumps(payload, indent=2, default=str)}

Diagnose the concrete failure, make the smallest repair needed inside the workspace, and rerun the most relevant checks.
Do not broaden scope, modify .env/secrets, revert unrelated user changes, deploy, publish, commit, reset git history, or perform external side effects.
If the failure is environmental or cannot be safely fixed in this repository, stop changing files and report the blocker clearly.
'''


class WorkerRunStore:
    """Durable state for bounded/resumable project-worker runs."""
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS worker_runs (
                id TEXT PRIMARY KEY, target TEXT NOT NULL, workspace TEXT NOT NULL, goal TEXT NOT NULL,
                status TEXT NOT NULL, attempts_json TEXT NOT NULL, baseline_json TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            )''')

    @contextmanager
    def _connect(self):
        """Short-lived connection that is always closed (Windows file locks)."""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create(self, target: str, workspace: str, goal: str, baseline: dict[str, Any]) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = int(time.time())
        with self._connect() as conn:
            conn.execute('INSERT INTO worker_runs VALUES (?,?,?,?,?,?,?,?,?)',
                         (run_id, target, workspace, goal, 'running', '[]', json.dumps(baseline), now, now))
        return self.get(run_id)

    def get(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM worker_runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return {
            'id': row[0], 'target': row[1], 'workspace': row[2], 'goal': row[3], 'status': row[4],
            'attempts': json.loads(row[5]), 'baseline': json.loads(row[6]),
            'created_at': row[7], 'updated_at': row[8],
        }

    def list(self, limit: int = 20) -> builtins.list[dict[str, Any]]:
        """Return recent worker runs without repository contents or secrets."""
        cap = max(1, min(int(limit), 100))
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT id,target,workspace,goal,status,attempts_json,created_at,updated_at FROM worker_runs ORDER BY updated_at DESC LIMIT ?',
                (cap,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                attempts = json.loads(row[5])
            except Exception:
                attempts = []
            out.append({
                'id': row[0], 'target': row[1], 'workspace': row[2], 'goal': row[3],
                'status': row[4], 'attempt_count': len(attempts),
                'created_at': row[6], 'updated_at': row[7],
            })
        return out

    def update(self, run_id: str, *, status: str | None = None, attempt: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.get(run_id)
        attempts = current['attempts']
        if attempt is not None:
            attempts.append(attempt)
        new_status = status or current['status']
        with self._connect() as conn:
            conn.execute('UPDATE worker_runs SET status=?, attempts_json=?, updated_at=? WHERE id=?',
                         (new_status, json.dumps(attempts, default=str), int(time.time()), run_id))
        return self.get(run_id)
