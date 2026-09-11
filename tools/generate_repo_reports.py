#!/usr/bin/env python3
"""Regenerate deterministic repository reports that tend to drift after refactors.

Usage:
  python tools/generate_repo_reports.py
  python tools/generate_repo_reports.py --check

The script intentionally avoids secrets/runtime data and only emits structural
metadata. CI can run --check to prevent docs from lagging the codebase.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api-gateway"
REPORTS = ROOT / "reports"
EXCLUDED_DIRS = {
    ".git", ".pytest_cache", "__pycache__", ".venv", "venv", "node_modules",
    "dist", "build", "coverage", ".next", "data",
    ".ruff_cache", ".hypothesis", ".report-runtime",
}


def _tree() -> str:
    lines = ["# Jarvis OS repository tree (generated)", "."]
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        depth = len(rel.parts)
        marker = "/" if path.is_dir() else ""
        lines.append(f"{'  ' * (depth - 1)}- {rel.as_posix()}{marker}")
    return "\n".join(lines) + "\n"


def _api_map() -> str:
    os.environ.setdefault("API_DATA_DIR", str(ROOT / ".report-runtime"))
    sys.path.insert(0, str(API))
    import main  # noqa: PLC0415
    from fastapi.routing import APIRoute  # noqa: PLC0415

    routes = [r for r in main.app.routes if isinstance(r, APIRoute)]
    routes.sort(key=lambda r: (r.path, ",".join(sorted(r.methods or []))))
    out = [
        "# Jarvis OS — API Map (generated)",
        "",
        f"**FastAPI application version:** `{main.APP_VERSION}`  ",
        f"**Registered API/UI routes:** **{len(routes)}**",
        "",
        "| Methods | Path | Name |",
        "|---|---|---|",
    ]
    for route in routes:
        methods = ", ".join(sorted((route.methods or set()) - {"HEAD", "OPTIONS"})) or "—"
        out.append(f"| {methods} | `{route.path}` | `{route.name}` |")
    out.append("")
    return "\n".join(out)



def _local_dependency_graph() -> tuple[dict[str, set[str]], list[list[str]]]:
    modules: dict[str, Path] = {}
    for path in API.glob("*.py"):
        modules[path.stem] = path
    for package in ("routes", "providers"):
        for path in (API / package).glob("*.py"):
            if path.stem != "__init__":
                modules[f"{package}.{path.stem}"] = path

    edges: dict[str, set[str]] = {name: set() for name in modules}
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in modules:
                        edges[name].add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module in modules:
                edges[name].add(node.module)

    visited: set[str] = set()
    stack: list[str] = []
    active: set[str] = set()
    cycles: list[list[str]] = []

    def walk(node: str) -> None:
        visited.add(node)
        stack.append(node)
        active.add(node)
        for nxt in edges[node]:
            if nxt not in visited:
                walk(nxt)
            elif nxt in active:
                start = stack.index(nxt)
                cycle = stack[start:] + [nxt]
                if cycle not in cycles:
                    cycles.append(cycle)
        active.remove(node)
        stack.pop()

    for node in edges:
        if node not in visited:
            walk(node)
    return edges, cycles

def _metrics() -> str:
    sys.path.insert(0, str(API))
    import main  # noqa: PLC0415
    from fastapi.routing import APIRoute  # noqa: PLC0415

    routes = [r for r in main.app.routes if isinstance(r, APIRoute)]
    route_groups = Counter()
    for route in routes:
        parts = [p for p in route.path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "v1":
            group = parts[1]
        elif parts:
            group = parts[0]
        else:
            group = "root"
        route_groups[group] += 1

    edges, cycles = _local_dependency_graph()
    module_lines = []
    for path in API.rglob("*.py"):
        module_lines.append((sum(1 for _ in path.open(encoding="utf-8")), path.relative_to(ROOT).as_posix()))
    module_lines.sort(reverse=True)

    payload = {
        "app_version": main.APP_VERSION,
        "registered_routes": len(routes),
        "api_gateway_python_files": len(list(API.rglob("*.py"))),
        "python_test_files": len(list((ROOT / "tests").glob("test_*.py"))),
        "main_py_lines": sum(1 for _ in (API / "main.py").open(encoding="utf-8")),
        "services_py_lines": sum(1 for _ in (API / "services.py").open(encoding="utf-8")),
        "local_import_cycle_count": len(cycles),
        "local_import_cycles": cycles,
        "largest_python_modules": [
            {"path": path, "lines": lines} for lines, path in module_lines[:10]
        ],
        "local_dependency_edges": {k: sorted(v) for k, v in sorted(edges.items()) if v},
        "route_groups": dict(sorted(route_groups.items())),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_or_check(path: Path, expected: str, check: bool) -> bool:
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if check:
        if current != expected:
            print(f"STALE: {path.relative_to(ROOT)}")
            return False
        print(f"OK: {path.relative_to(ROOT)}")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(expected, encoding="utf-8")
    print(f"WROTE: {path.relative_to(ROOT)}")
    return True


def main_cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when generated reports differ")
    args = parser.parse_args()
    REPORTS.mkdir(parents=True, exist_ok=True)
    ok = True
    ok &= _write_or_check(REPORTS / "repo-tree.txt", _tree(), args.check)
    ok &= _write_or_check(REPORTS / "api-map.md", _api_map(), args.check)
    ok &= _write_or_check(REPORTS / "repo-metrics.json", _metrics(), args.check)
    runtime = ROOT / ".report-runtime"
    if runtime.exists():
        import shutil
        shutil.rmtree(runtime, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
