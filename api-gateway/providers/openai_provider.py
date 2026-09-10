from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from .base import ProviderAdapter, ProviderMessage, ProviderResult


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


class OpenAIProvider(ProviderAdapter):
    """Official OpenAI API adapter using the Responses API over HTTP.

    This intentionally does not depend on the OpenAI Python SDK so Jarvis can
    keep its current lightweight dependency footprint.
    """

    name = "openai"
    kind = "cloud"
    supports_stream = False
    supports_tools = True

    def __init__(self) -> None:
        super().__init__(_env("OPENAI_MODEL", "gpt-5.6"))
        self.api_key = _env("OPENAI_API_KEY")
        self.base_url = _env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.reasoning_effort = _env("OPENAI_REASONING_EFFORT", "medium")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _input(messages: List[ProviderMessage]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for msg in messages[-100:]:
            role = msg.role if msg.role in {"system", "user", "assistant"} else "user"
            out.append({"role": role, "content": msg.content})
        return out

    @staticmethod
    def _output_text(data: Dict[str, Any]) -> str:
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        chunks: List[str] = []
        for item in data.get("output") or []:
            for content in item.get("content") or []:
                if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        return "".join(chunks)

    async def complete(self, messages: List[ProviderMessage], *, temperature: float = 0.7, max_tokens: int = 1024, task_context: Optional[Dict[str, Any]] = None) -> ProviderResult:
        if not self.configured:
            raise RuntimeError("openai not configured")
        payload: Dict[str, Any] = {
            "model": self.model,
            "input": self._input(messages),
            "max_output_tokens": max(1, min(int(max_tokens), 32768)),
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        # Reasoning model families may reject temperature; omit it by default.
        if _env("OPENAI_SEND_TEMPERATURE", "false").lower() in {"1", "true", "yes"}:
            payload["temperature"] = max(0.0, min(float(temperature), 2.0))
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{self.base_url}/responses",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code not in (200, 201):
            raise RuntimeError(f"openai {response.status_code}: {response.text[:300]}")
        data = response.json()
        text = self._output_text(data)
        if not text:
            raise RuntimeError("openai returned no text output")
        return ProviderResult(
            text,
            self.name,
            self.model,
            usage=data.get("usage") or {},
            metadata={"response_id": data.get("id")},
        )

    async def health(self) -> Dict[str, Any]:
        if not self.configured:
            return {"online": False, "configured": False, "reason": "missing OPENAI_API_KEY"}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            return {"online": response.status_code == 200, "configured": True, "status_code": response.status_code}
        except Exception as exc:
            return {"online": False, "configured": True, "error": str(exc)}
