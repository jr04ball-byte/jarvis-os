import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api-gateway'))
import tools


def test_file_search_and_content_search():
    with tempfile.TemporaryDirectory() as d:
        old = tools.HOME_ROOTS
        tools.HOME_ROOTS = [Path(d)]
        try:
            p=Path(d)/'notes.txt'; p.write_text('alpha secret project', encoding='utf-8')
            assert tools.file_search('notes')
            assert 'secret' in tools.file_content_search('secret')[0]['snippet']
            assert tools.read_file(str(p))['content'] == 'alpha secret project'
        finally: tools.HOME_ROOTS=old

def test_path_escape_blocked():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
        old=tools.HOME_ROOTS; tools.HOME_ROOTS=[Path(d)]
        try:
            try: tools.read_file(str(Path(outside)/'x.txt'))
            except PermissionError: pass
            else: raise AssertionError('path escape was not blocked')
        finally: tools.HOME_ROOTS=old

async def _ha_test():
    try: await tools.ha_device('light.bad','on',evil=1)
    except RuntimeError: pass
    except ValueError: pass
    else: raise AssertionError('unconfigured HA should not execute')

def test_ha_requires_config(): asyncio.run(_ha_test())

def test_local_tool_scanner_shape(monkeypatch):
    import local_tools
    monkeypatch.setattr(local_tools.platform, 'system', lambda: 'Windows')
    result = local_tools.scan_local_tools()
    assert 'tools' in result
    assert isinstance(result['tools'], list)
    names = {x['name'] for x in result['tools']}
    assert {'Blender', 'Unreal Engine 5', 'ComfyUI', 'FFmpeg', 'Ollama', 'Python'} <= names
    for item in result['tools']:
        assert item['status'] in {'ready', 'not_found'}
        assert isinstance(item['capabilities'], list)

def test_scanner_cache_is_platform_aware(monkeypatch):
    """Regression: a Linux-poisoned cache must not satisfy a Windows scan.
    (Broke CI when an earlier test populated the cache on ubuntu.)"""
    import local_tools
    monkeypatch.setattr(local_tools.platform, 'system', lambda: 'Linux')
    assert local_tools.scan_local_tools()['tools'] == []
    monkeypatch.setattr(local_tools.platform, 'system', lambda: 'Windows')
    result = local_tools.scan_local_tools()
    assert result['platform'] == 'Windows'
    names = {x['name'] for x in result['tools']}
    assert {'Blender', 'Unreal Engine 5', 'ComfyUI', 'FFmpeg', 'Ollama', 'Python'} <= names

def test_adaptive_compute_manager_has_safe_modes(monkeypatch):
    import compute_manager
    monkeypatch.setattr(compute_manager, 'gpu_stats', lambda: {
        'available': True, 'name': 'RTX 3070', 'memory_used_mb': 7900,
        'memory_total_mb': 8192, 'memory_free_mb': 292,
        'utilization_gpu': 90, 'temperature_c': 70,
    })
    selected = compute_manager.select_mode('auto')
    assert selected['mode'] == 'gpu_guarded'
    assert selected['options'] == {}

    selected = compute_manager.select_mode('cpu')
    assert selected['mode'] == 'cpu'
    assert selected['options']['num_gpu'] == 0

    selected = compute_manager.select_mode('hybrid')
    assert selected['mode'] == 'hybrid'
    assert selected['options']['num_gpu'] >= 1
