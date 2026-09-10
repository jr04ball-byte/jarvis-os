from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator


@dataclass
class ProviderMessage:
    role: str
    content: str


@dataclass
class ProviderResult:
    content: str
    provider: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderAdapter(ABC):
    """Common contract used by Jarvis for every intelligence provider.

    Providers reason; Jarvis owns routing, memory, permissions and execution.
    """

    name: str = "unknown"
    kind: str = "cloud"
    supports_stream: bool = False
    supports_tools: bool = False
    supports_local: bool = False

    def __init__(self, model: str) -> None:
        self.model = model

    @property
    def configured(self) -> bool:
        return True

    @abstractmethod
    async def complete(
        self,
        messages: list[ProviderMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        task_context: dict[str, Any] | None = None,
    ) -> ProviderResult:
        raise NotImplementedError

    async def stream(
        self,
        messages: list[ProviderMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        task_context: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        result = await self.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            task_context=task_context,
        )
        yield result.content

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "model": self.model,
            "configured": self.configured,
            "supports_stream": self.supports_stream,
            "supports_tools": self.supports_tools,
            "supports_local": self.supports_local,
        }
