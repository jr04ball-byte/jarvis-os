"""Authoritative tool registry for Jarvis OS.

Single source of truth for tool names, descriptions, JSON schemas,
handler dispatch, and auth-policy references. The LLM-facing tool
list and the execution router both derive from this registry; neither
invents entries independently. Auth policy remains in security.py
(this module references it, never duplicates it).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

from security import allowed, requires_confirmation, risk

logger = logging.getLogger(__name__)


class ToolNotFoundError(Exception):
    """Raised when a tool name has no registered entry."""


class ToolArgsError(Exception):
    """Raised when arguments cannot be parsed or are missing required keys."""


ToolHandler = Callable[..., Awaitable[dict[str, Any]]]


class ToolEntry:
    __slots__ = ("name", "description", "parameters", "handler_name", "confirmation")

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None,
        handler_name: str,
        confirmation: bool,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters or {}
        self.handler_name = handler_name
        self.confirmation = confirmation


def _validate_schema(args, schema):
    from schema_validation import validate
    try:
        validate(args, schema)
    except ValueError as exc:
        raise ToolArgsError(str(exc)) from exc


class ToolRegistry:
    """Authoritative registry of all Jarvis tools.

    Tools are registered with a canonical name, an LLM description,
    a JSON Schema for arguments, a handler-name string that maps to a
    function in ``services`` / ``tools``, and a confirmation flag that
    is derived from the auth policy in ``security`` (never stored
    redundantly).
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None,
        handler_name: str,
    ) -> None:
        if not allowed(name):
            raise ValueError(f"cannot register disallowed tool: {name}")
        self._tools[name] = ToolEntry(
            name=name,
            description=description,
            parameters=parameters,
            handler_name=handler_name,
            confirmation=requires_confirmation(name),
        )

    def register_handler(self, handler_name: str, handler: ToolHandler) -> None:
        self._handlers[handler_name] = handler

    def has(self, name: str) -> bool:
        return name in self._tools

    def get_entry(self, name: str) -> ToolEntry:
        if name not in self._tools:
            raise ToolNotFoundError(f"unknown tool: {name}")
        return self._tools[name]

    def build_tools_list(self) -> list[dict[str, Any]]:
        """Return [{name, description, confirmation}] for allowed tools."""
        out: list[dict[str, Any]] = []
        for name in sorted(self._tools):
            entry = self._tools[name]
            func: dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": entry.description,
                },
            }
            if entry.parameters:
                func["function"]["parameters"] = entry.parameters
            out.append(func)
        return out

    def build_tool_summaries(self) -> list[dict[str, Any]]:
        """Return [{name, description, confirmation}] for allowed tools (flat)."""
        return [
            {"name": e.name, "description": e.description, "confirmation": e.confirmation}
            for e in self._tools.values()
        ]

    def resolve_handler(self, name: str) -> ToolHandler:
        entry = self.get_entry(name)
        handler = self._handlers.get(entry.handler_name)
        if handler is None:
            raise ToolNotFoundError(f"no handler for tool {name}: {entry.handler_name}")
        return handler

    def validate_args(self, name: str, raw: Any) -> dict[str, Any]:
        entry = self.get_entry(name)
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ToolArgsError(f"invalid tool arguments for {name}")
        if not isinstance(args, dict):
            raise ToolArgsError(f"tool arguments for {name} must be an object")
        schema = entry.parameters
        if schema:
            _validate_schema(args, schema)
        return args

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Dispatch a tool call through the registry.

        Returns the handler result dict. Raises HTTPException for
        auth/validation/transport errors; raises ToolNotFoundError /
        ToolArgsError for caller errors.
        """
        if not allowed(name):
            from fastapi import HTTPException
            raise HTTPException(404, "unknown or disallowed tool")
        entry = self.get_entry(name)
        if entry.confirmation and not confirmed:
            from fastapi import HTTPException
            raise HTTPException(
                403,
                "confirmation required before executing this tool",
            )
        args = self.validate_args(name, args)
        handler = self.resolve_handler(name)
        return await handler(args)


TOOL_REGISTRY = ToolRegistry()
