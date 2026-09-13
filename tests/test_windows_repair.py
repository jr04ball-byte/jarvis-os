import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host-bridge'))
sys.path.insert(0, str(ROOT / 'api-gateway'))


def test_blender_versioned_foundation_directory(monkeypatch, tmp_path):
    import blender_worker as worker
    exe = tmp_path / 'Blender Foundation' / 'Blender 5.2' / 'blender.exe'
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(worker, '_which', lambda _: None)
    monkeypatch.setattr(worker, '_first_file', lambda _: None)
    monkeypatch.setenv('ProgramFiles', str(tmp_path))
    assert worker._find_blender() == exe


@pytest.mark.parametrize('second_exit,expected_status', [(1, 'failed'), (0, 'success')])
def test_blender_script_error_repair_is_bounded(monkeypatch, tmp_path, second_exit, expected_status):
    import blender_worker as worker
    import creative_script
    monkeypatch.setitem(sys.modules, 'PIL', SimpleNamespace(Image=SimpleNamespace(
        open=lambda _: SimpleNamespace(verify=lambda: None))))
    outputs = {}
    launches = []

    async def generate(goal, blend, render):
        outputs.update(blend=blend, render=render)
        return 'raise RuntimeError("bad script")'

    async def communicate():
        outputs['blend'].write_bytes(b'BLENDER')
        outputs['render'].write_bytes(b'verified image fixture')
        return b'', b'Traceback: RuntimeError: bad script'

    async def spawn(*args, **kwargs):
        assert args[args.index('--python-exit-code') + 1] == '1'
        assert Path(args[-1]).is_absolute()
        launches.append(args)
        return SimpleNamespace(returncode=1 if len(launches) == 1 else second_exit, communicate=communicate)

    monkeypatch.setattr(worker, '_find_blender', lambda: tmp_path / 'blender.exe')
    monkeypatch.setattr(worker, '_version', lambda _: 'test')
    monkeypatch.setattr(creative_script, 'generate', generate)
    monkeypatch.setattr(worker.asyncio, 'create_subprocess_exec', spawn)
    result = asyncio.run(worker.run_blender_work('test', str(tmp_path)))
    assert result['status'] == expected_status
    assert len(launches) == 2
    assert bool(result.get('artifacts')) is (expected_status == 'success')


def test_incomplete_gemini_scene_is_retried_then_rejected(monkeypatch, tmp_path):
    import brains
    import creative_script
    calls = []

    async def complete(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content='import bpy\nx = 4.', metadata={'finish_reason': 'MAX_TOKENS'})

    monkeypatch.setattr(brains, 'providers', {'gemini': SimpleNamespace(configured=True, complete=complete)})
    with pytest.raises(ValueError, match='incomplete scene twice'):
        asyncio.run(creative_script.generate('throne hall', tmp_path/'scene.blend', tmp_path/'render.png'))
    assert len(calls) == 2
    assert not (tmp_path/'scene.blend').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows host bridge')
@pytest.mark.parametrize('process,expected,new_window', [('SystemSettings.exe', True, False), ('explorer.exe', False, False), ('ApplicationFrameHost.exe', True, True)])
def test_settings_verifies_existing_window_not_explorer(monkeypatch, process, expected, new_window):
    import host_bridge as bridge
    psutil = SimpleNamespace(NoSuchProcess=ProcessLookupError, AccessDenied=PermissionError)
    monkeypatch.setitem(sys.modules, 'psutil', psutil)
    monkeypatch.setattr(bridge, 'computer_auth', lambda _: None)
    monkeypatch.setattr(bridge, '_audit', lambda *a, **k: None)
    window = {'handle': 123, 'pid': 42, 'title': 'Settings', 'rect': [0, 0, 800, 600]}
    snapshots = iter([[], [window]]) if new_window else None
    monkeypatch.setattr(bridge, '_iter_windows', lambda: next(snapshots, [window]) if snapshots else [window])
    monkeypatch.setattr(bridge, '_fg_info', lambda: {'handle': 999} if new_window else window)
    monkeypatch.setattr(bridge, '_focus_element', lambda _: False)
    psutil.process_iter = lambda _: [SimpleNamespace(pid=42)]
    psutil.Process = lambda _: SimpleNamespace(name=lambda: process)
    launched = []
    monkeypatch.setattr(bridge.os, 'startfile', launched.append)
    monkeypatch.setattr(bridge.time, 'sleep', lambda _: None)
    ticks = iter([0, 0, 21])
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: next(ticks))
    result = bridge.computer_open(bridge.ComputerOpenRequest(target='computer settings'))
    assert launched == ['ms-settings:']
    assert result['ok'] is expected
    if expected:
        assert result['verified_via'] == 'visible_window'


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows API')
@pytest.mark.skipif(os.getenv('JARVIS_DESKTOP_TEST') != '1', reason='Requires interactive Windows desktop')
def test_native_enumeration_from_threadpool():
    from concurrent.futures import ThreadPoolExecutor

    from windows_native import windows
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(windows).result(timeout=10)
    assert isinstance(result, list)
    for window in result:
        assert window['handle'] > 0 and window['pid'] > 0
        assert len(window['rect']) == 4

def test_resolve_open_target_creative_files_url_rewrites_to_local(monkeypatch):
    import host_bridge
    monkeypatch.setattr(host_bridge, '_artifact_scene', lambda task_id: 'C:\\scenes\\scene.blend')
    kind, value = host_bridge._resolve_open_target('http://localhost:8000/v1/creative-files/task_a820ddc7/scene.blend')
    assert kind == 'local' and value == 'C:\\scenes\\scene.blend'


def test_resolve_open_target_plain_task_reference(monkeypatch):
    import host_bridge
    monkeypatch.setattr(host_bridge, '_artifact_scene', lambda task_id: 'C:\\scenes\\scene.blend')
    assert host_bridge._resolve_open_target('task_281606a7') == ('local', 'C:\\scenes\\scene.blend')
    assert host_bridge._resolve_open_target('a820ddc7') == ('local', 'C:\\scenes\\scene.blend')


def test_resolve_open_target_remote_url_kept_for_rejection():
    import host_bridge
    kind, value = host_bridge._resolve_open_target('https://example.com/foo.blend')
    assert kind == 'remote' and value == 'https://example.com/foo.blend'


def test_resolve_open_target_absolute_existing_file_is_local(tmp_path):
    import host_bridge
    target = tmp_path / 'scene.blend'
    target.write_bytes(b'BLENDER')
    assert host_bridge._resolve_open_target(str(target)) == ('local', str(target))


def test_resolve_open_target_empty_is_none():
    import host_bridge
    assert host_bridge._resolve_open_target('') is None
    assert host_bridge._resolve_open_target('   ') is None
