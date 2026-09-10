from __future__ import annotations

import json
import os
from typing import Any, AsyncGenerator

import httpx

from .base import ProviderAdapter, ProviderMessage, ProviderResult


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


class OllamaProvider(ProviderAdapter):
    name = "ollama"
    kind = "local"
    supports_stream = True
    supports_tools = True
    supports_local = True

    def __init__(self) -> None:
        super().__init__(_env("JARVIS_DEEP_MODEL", "qwen3.5:9b"))
        self.fast_model = _env("JARVIS_FAST_MODEL", "gemma3:4b")
        self.base_url = _env("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")

    @staticmethod
    def _messages(messages: list[ProviderMessage]) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in messages[-100:]]

    def select_model(self, task_context: dict[str, Any] | None) -> str:
        if (task_context or {}).get("speed_priority", 0) >= 7 or (task_context or {}).get("mode") == "fast":
            return self.fast_model
        return self.model

    async def complete(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int = 1024, task_context: dict[str, Any] | None = None) -> ProviderResult:
        model = self.select_model(task_context)
        payload = {
            "model": model,
            "messages": self._messages(messages),
            "stream": False,
            "think": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"ollama {response.status_code}: {response.text[:200]}")
        data = response.json()
        text = (data.get("message") or {}).get("content", "")
        return ProviderResult(
            text,
            self.name,
            model,
            usage={
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            },
        )

    async def stream(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int = 1024, task_context: dict[str, Any] | None = None) -> AsyncGenerator[str, None]:
        model = self.select_model(task_context)
        payload = {
            "model": model,
            "messages": self._messages(messages),
            "stream": True,
            "think": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"ollama {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = (data.get("message") or {}).get("content", "")
                    if text:
                        yield text
                    if data.get("done"):
                        break

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
            return {"online": response.status_code == 200, "configured": True, "status_code": response.status_code}
        except Exception as exc:
            return {"online": False, "configured": True, "error": str(exc)}
