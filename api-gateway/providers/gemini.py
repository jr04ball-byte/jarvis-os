from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from .base import ProviderAdapter, ProviderMessage, ProviderResult


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _text_parts(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    out = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            out.append(part["text"])
    return "".join(out)


class GeminiProvider(ProviderAdapter):
    name = "gemini"
    kind = "cloud"
    supports_stream = True
    supports_tools = True

    def __init__(self, model: str | None = None) -> None:
        super().__init__(model or _env("GEMINI_MODEL", "gemini-3.5-flash"))
        self.api_key = _env("GEMINI_API_KEY")
        self.base_url = _env("GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _contents(messages: list[ProviderMessage]) -> list[dict[str, Any]]:
        contents = []
        system_text = []
        for msg in messages[-100:]:
            if msg.role == "system":
                system_text.append(msg.content)
                continue
            role = "model" if msg.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg.content}]})
        if system_text:
            prefix = "\n\n".join(system_text)
            if contents:
                contents[0]["parts"][0]["text"] = prefix + "\n\n" + contents[0]["parts"][0]["text"]
            else:
                contents.append({"role": "user", "parts": [{"text": prefix}]})
        return contents

    async def complete(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int = 1024, task_context: dict[str, Any] | None = None) -> ProviderResult:
        if not self.configured:
            raise RuntimeError("gemini not configured")
        payload = {
            "contents": self._contents(messages),
            "generationConfig": {
                "temperature": max(0.0, min(float(temperature), 2.0)),
                "maxOutputTokens": max(1, min(int(max_tokens), 32768)),
            },
        }
        url = f"{self.base_url}/models/{self.model}:generateContent"
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                url,
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code != 200:
            raise RuntimeError(f"gemini {response.status_code}: {response.text[:300]}")
        data = response.json()
        candidates = data.get("candidates") or [{}]
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        usage = data.get("usageMetadata") or {}
        return ProviderResult(_text_parts(parts), self.name, self.model, usage=usage)

    async def stream(self, messages: list[ProviderMessage], *, temperature: float = 0.7, max_tokens: int = 1024, task_context: dict[str, Any] | None = None) -> AsyncGenerator[str, None]:
        if not self.configured:
            raise RuntimeError("gemini not configured")
        payload = {
            "contents": self._contents(messages),
            "generationConfig": {
                "temperature": max(0.0, min(float(temperature), 2.0)),
                "maxOutputTokens": max(1, min(int(max_tokens), 32768)),
            },
        }
        url = f"{self.base_url}/models/{self.model}:streamGenerateContent?alt=sse"
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                url,
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise RuntimeError(f"gemini {response.status_code}: {body[:300]!r}")
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
                    candidates = data.get("candidates") or [{}]
                    parts = ((candidates[0].get("content") or {}).get("parts")) or []
                    text = _text_parts(parts)
                    if text:
                        yield text

    async def health(self) -> dict[str, Any]:
        if not self.configured:
            return {"online": False, "configured": False, "reason": "missing GEMINI_API_KEY"}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/models", params={"key": self.api_key})
            return {"online": response.status_code == 200, "configured": True, "status_code": response.status_code}
        except Exception as exc:
            return {"online": False, "configured": True, "error": str(exc)}
