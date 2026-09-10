"""Cycle-3: provider-interface conformance contracts (Master Goal 2)."""
import asyncio
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

from providers import (
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenCodeProvider,
    ProviderAdapter,
    ProviderMessage,
    ProviderResult,
)
from providers.base import ProviderAdapter as BaseAdapter

ALL = (GeminiProvider, OllamaProvider, OpenAIProvider, OpenCodeProvider)
KEY_VARS = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_MODEL",
    "OPENAI_MODEL",
    "JARVIS_DEEP_MODEL",
    "JARVIS_FAST_MODEL",
    "OPENCODE_MODEL",
    "OLLAMA_URL",
    "GEMINI_BASE",
    "OPENAI_BASE_URL",
    "OPENCODE_SERVER_URL",
)


@pytest.fixture
def clean_env(monkeypatch):
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)


def test_all_providers_satisfy_abc(clean_env):
    for cls in ALL:
        provider = cls()
        assert isinstance(provider, ProviderAdapter)
        assert isinstance(provider.model, str) and provider.model


def test_uniform_construction_with_model_override(clean_env):
    assert GeminiProvider(model="override-gem").model == "override-gem"
    assert OpenAIProvider(model="override-gpt").model == "override-gpt"
    assert OllamaProvider(model="override-ollama").model == "override-ollama"
    assert OpenCodeProvider(model="override-worker").model.endswith("override-worker")


def test_complete_health_stream_signatures_match_base():
    base_complete = inspect.signature(BaseAdapter.complete)
    base_health = inspect.signature(BaseAdapter.health)
    base_stream = inspect.signature(BaseAdapter.stream)
    for cls in ALL:
        assert inspect.signature(cls.complete) == base_complete, cls.__name__
        assert inspect.signature(cls.health) == base_health, cls.__name__
    for cls in (GeminiProvider, OllamaProvider):
        assert inspect.signature(cls.stream) == base_stream, cls.__name__


def test_describe_shape(clean_env):
    for cls in ALL:
        desc = cls().describe()
        assert set(desc) == {
            "name", "kind", "model", "configured",
            "supports_stream", "supports_tools", "supports_local",
        }
        assert isinstance(desc["configured"], bool)


def test_configured_flags_without_keys(clean_env):
    assert GeminiProvider().configured is False
    assert OpenAIProvider().configured is False
    assert OllamaProvider().configured is True


def test_result_positional_field_order():
    result = ProviderResult("content", "provider", "model")
    assert (result.content, result.provider, result.model) == ("content", "provider", "model")
    assert result.usage == {} and result.metadata == {}


def test_unconfigured_guards_raise_offline(clean_env):
    async def run():
        with pytest.raises(RuntimeError, match="not configured"):
            await GeminiProvider().complete([ProviderMessage("user", "hi")])
        with pytest.raises(RuntimeError, match="not configured"):
            await OpenAIProvider().complete([ProviderMessage("user", "hi")])

    asyncio.run(run())


def test_opencode_requires_workspace_offline(clean_env):
    async def run():
        with pytest.raises(RuntimeError, match="workspace"):
            await OpenCodeProvider().complete([ProviderMessage("user", "hi")], task_context={})

    asyncio.run(run())


def test_health_never_raises(clean_env):
    async def run():
        for cls in ALL:
            result = await cls().health()
            assert isinstance(result.get("online"), bool)

    asyncio.run(run())


def test_base_stream_fallback_yields_complete_content():
    class FakeProvider(ProviderAdapter):
        async def complete(self, messages, *, temperature=0.7, max_tokens=1024, task_context=None):
            return ProviderResult("hello world", "fake", "fake-model")

        async def health(self):
            return {"online": True, "configured": True}

    async def run():
        return [c async for c in FakeProvider("fake-model").stream([ProviderMessage("user", "hi")])]

    assert "".join(asyncio.run(run())) == "hello world"
