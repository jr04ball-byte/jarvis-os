"""V24 P6: model_lab offline coverage (degraded runtimes + mocked HTTP)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import model_lab
import pytest


def test_overview_offline_returns_degraded_structure():
    async def run():
        return await model_lab.overview()

    out = asyncio.run(run())
    assert set(out) == {"timestamp", "ollama", "lmstudio", "targets"}
    assert isinstance(out["targets"], list) and out["targets"]
    assert set(out["ollama"]) >= {"online", "models"}


def test_hf_search_maps_rows_and_clamps_limit(monkeypatch):
    seen = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return [{"id": "m1", "downloads": 5, "likes": 1, "pipeline_tag": "p",
                     "library_name": "l", "lastModified": "t"}]

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, *, params=None, **kwargs):
            seen["url"] = url
            seen["params"] = params
            return FakeResponse()

    monkeypatch.setattr(model_lab.httpx, "AsyncClient", FakeClient)

    async def run():
        return await model_lab.hf_search("qwen", limit=99)

    out = asyncio.run(run())
    assert out["query"] == "qwen" and out["models"][0]["id"] == "m1"
    assert seen["params"]["limit"] == 20
    assert "huggingface.co" in seen["url"]


def test_benchmark_rejects_unknown_provider():
    async def run():
        with pytest.raises(ValueError):
            await model_lab.benchmark("nope", "m", "hi")

    asyncio.run(run())


def test_lm_load_clamps_payload(monkeypatch):
    seen = {}

    class FakeResponse:
        status_code = 200
        content = b"{}"
        is_success = True

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json=None, **kwargs):
            seen["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(model_lab.httpx, "AsyncClient", FakeClient)

    async def run():
        return await model_lab.lm_load("m", context_length=100, gpu="bogus", ttl=0)

    out = asyncio.run(run())
    assert out["ok"] is True
    assert seen["payload"]["context_length"] == 1024
    assert "gpu" not in seen["payload"]
    assert "ttl" not in seen["payload"]
