from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from .base import ProviderAdapter, ProviderMessage, ProviderResult
from workspace_registry import is_registered_workspace


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


class OpenCodeProvider(ProviderAdapter):
    name = "opencode"
    kind = "worker"
    supports_stream = False
    supports_tools = True
    supports_local = True

    def __init__(self) -> None:
        self.base_url = _env("OPENCODE_SERVER_URL", "http://127.0.0.1:4096").rstrip("/")
        self.provider_id = _env("OPENCODE_PROVIDER", "opencode-go")
        model_id = _env("OPENCODE_MODEL", "deepseek-v4-flash")
        self.model_id = model_id
        super().__init__(f"{self.provider_id}/{model_id}")
        self.agent = _env("OPENCODE_AGENT")

    @staticmethod
    def _content(messages: List[ProviderMessage]) -> str:
        lines = []
        for msg in messages:
            role = {"assistant": "Assistant", "system": "System", "user": "User"}.get(msg.role, msg.role)
            lines.append(f"{role}: {msg.content}")
        return "\n\n".join(lines)

    async def complete(self, messages: List[ProviderMessage], *, temperature: float = 0.7, max_tokens: int = 1024, task_context: Optional[Dict[str, Any]] = None) -> ProviderResult:
        headers = {"Content-Type": "application/json"}
        # OpenCode scopes the server instance to this directory using the
        # x-opencode-directory header. This makes the workspace boundary real,
        # not merely an instruction in the prompt.
        task_context = task_context or {}
        workspace = str(task_context.get("workspace") or "").strip()
        permission = str(task_context.get("permission") or "read_only").strip().lower()
        if permission not in {"read_only", "workspace_write"}:
            raise RuntimeError(f"unsupported opencode permission: {permission}")
        if not workspace:
            raise RuntimeError("opencode requires a registered workspace")
        if not is_registered_workspace(workspace):
            raise RuntimeError("opencode workspace is not registered with Jarvis")
        headers["x-opencode-directory"] = workspace
        username = _env("OPENCODE_SERVER_USERNAME", "opencode")
        password = _env("OPENCODE_SERVER_PASSWORD")
        model = {"providerID": self.provider_id, "modelID": self.model_id}
        system = (
            "You are Jarvis's software-engineering worker. Work only inside the supplied task scope. "
            "Jarvis owns permissions and execution authority. Never broaden scope, expose credentials, "
            "or modify .env/secrets unless the task explicitly authorizes it. "
            f"Current permission: {permission}."
        )
        if workspace:
            system += f" Workspace: {workspace}."
        auth = httpx.BasicAuth(username, password) if password else None
        async with httpx.AsyncClient(timeout=240.0, auth=auth) as client:
            session = await client.post(
                f"{self.base_url}/session",
                headers=headers,
                json={"title": "jarvis-worker"},
            )
            if session.status_code not in (200, 201):
                raise RuntimeError(f"opencode session {session.status_code}: {session.text[:200]}")
            session_id = session.json().get("id")
            body: Dict[str, Any] = {
                "model": model,
                "system": system,
                "parts": [{"type": "text", "text": self._content(messages)}],
            }
            if self.agent:
                body["agent"] = self.agent
            response = await client.post(
                f"{self.base_url}/session/{session_id}/message",
                headers=headers,
                json=body,
            )
        if response.status_code != 200:
            raise RuntimeError(f"opencode message {response.status_code}: {response.text[:300]}")
        data = response.json()
        text = _text_parts(data.get("parts"))
        if not text:
            text = _text_parts((data.get("info") or {}).get("parts"))
        return ProviderResult(text, self.name, self.model, metadata={"session_id": session_id})

    async def health(self) -> Dict[str, Any]:
        try:
            username = _env("OPENCODE_SERVER_USERNAME", "opencode")
            password = _env("OPENCODE_SERVER_PASSWORD")
            auth = httpx.BasicAuth(username, password) if password else None
            async with httpx.AsyncClient(timeout=3.0, auth=auth) as client:
                response = await client.get(f"{self.base_url}/global/health")
            return {"online": response.status_code < 500, "configured": True, "status_code": response.status_code}
        except Exception as exc:
            return {"online": False, "configured": True, "error": str(exc)}
