"""Review then stage an upgrade in a NEW sibling directory; never overwrite live files."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

IGNORED = {'.git', '__pycache__', '.pytest_cache', '.venv', 'node_modules', 'data'}


def inventory(root):
    result = {}
    for path in root.rglob('*'):
        rel = path.relative_to(root)
        if any(p in IGNORED for p in rel.parts) or path.name == '.env':
            continue
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Linked paths are not accepted')
        if path.is_file():
            result[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def plan(current, release):
    old, new = inventory(current), inventory(release)
    return {'current': str(current), 'release': str(release), 'hashes': new,
            'baseline': old, 'changed': sorted(k for k in new if old.get(k) != new[k]),
            'removed': sorted(old.keys()-new.keys()),
            'runtime': 'Existing .env and data remain in the original installation; migrate offline after review.'}


def stage(current, release, destination, approved):
    if current == release or current in release.parents or release in current.parents:
        raise ValueError('Current and release directories must be separate')
    if destination.exists() or current == destination or current in destination.parents or release in destination.parents:
        raise ValueError('Destination must be a new directory outside both source trees')
    if plan(current, release) != approved:
        raise ValueError('Files changed after review; generate a new plan')
    destination.mkdir(parents=True)
    for name in approved['hashes']:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(release / name, target)
    if inventory(destination) != approved['hashes']:
        raise ValueError('Staged hashes failed verification; do not activate candidate')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('current', type=Path)
    parser.add_argument('release', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--stage', type=Path)
    args = parser.parse_args()
    current, release = args.current.resolve(), args.release.resolve()
    if not current.is_dir() or not release.is_dir():
        parser.error('Both source directories must exist')
    if args.stage:
        stage(current, release, args.stage.resolve(), json.loads(args.plan.read_text(encoding='utf-8')))
    else:
        args.plan.write_text(json.dumps(plan(current, release), indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
