from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from .base import ProviderAdapter, ProviderMessage, ProviderResult


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


class OpenRouterProvider(ProviderAdapter):
    """OpenAI-compatible OpenRouter adapter used as Gemini's cloud fallback."""

    name = "openrouter"
    kind = "cloud"
    supports_stream = True
    supports_tools = True

    def __init__(self, model: str | None = None) -> None:
        super().__init__(model or _env("OPENROUTER_MODEL", "openrouter/free"))
        self.api_key = _env("OPENROUTER_API_KEY")
        self.base_url = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _messages(messages: list[ProviderMessage]) -> list[dict[str, str]]:
        return [
            {"role": msg.role if msg.role in {"system", "user", "assistant"} else "user", "content": msg.content}
            for msg in messages[-100:]
        ]

    def _payload(self, messages: list[ProviderMessage], temperature: float, max_tokens: int, *, stream: bool) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": self._messages(messages),
            "temperature": max(0.0, min(float(temperature), 2.0)),
            "max_tokens": max(1, min(int(max_tokens), 32768)),
            "stream": stream,
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _env("OPENROUTER_SITE_URL", "http://127.0.0.1:8000"),
            "X-Title": _env("OPENROUTER_APP_NAME", "Jarvis OS"),
        }

    async def complete(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int = 1024, task_context: dict[str, Any] | None = None) -> ProviderResult:
        if not self.configured:
            raise RuntimeError("openrouter not configured")
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=self._payload(messages, temperature, max_tokens, stream=False))
        if response.status_code != 200:
            raise RuntimeError(f"openrouter {response.status_code}: {response.text[:300]}")
        data = response.json()
        choices = data.get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
        if not content:
            raise RuntimeError("openrouter returned no text output")
        return ProviderResult(content, self.name, data.get("model") or self.model, usage=data.get("usage") or {}, metadata={"response_id": data.get("id")})

    async def stream(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int = 1024, task_context: dict[str, Any] | None = None) -> AsyncGenerator[str, None]:
        if not self.configured:
            raise RuntimeError("openrouter not configured")
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=self._payload(messages, temperature, max_tokens, stream=True)) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise RuntimeError(f"openrouter {response.status_code}: {body[:300]!r}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices") or []
                    text = ((choices[0].get("delta") or {}).get("content") if choices else "") or ""
                    if text:
                        yield text

    async def health(self) -> dict[str, Any]:
        if not self.configured:
            return {"online": False, "configured": False, "reason": "missing OPENROUTER_API_KEY"}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/models", headers=self._headers())
            return {"online": response.status_code == 200, "configured": True, "status_code": response.status_code}
        except Exception as exc:
            return {"online": False, "configured": True, "error": str(exc)}
