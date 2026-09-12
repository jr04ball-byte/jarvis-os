"""V25 addition: structured tool result for the agent loop.

This module is intentionally small — it only defines the ToolResult
dataclass. Services.py imports and uses it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Structured result of a single tool execution within the agent loop.

    Unlike raw dicts returned by _agent_tool_impl, this type makes the
    success/failure state explicit so the loop can apply bounded retries
    and never report a failed tool as completed.
    """

    status: str  # "success" | "error" | "confirmation_required"
    tool: str
    execution_id: str = ""
    result: Any = None
    error: str | None = None
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def success(cls, tool: str, result: Any = None, execution_id: str = "", **extra) -> "ToolResult":
        return cls(status="success", tool=tool, result=result, execution_id=execution_id, **extra)

    @classmethod
    def failure(cls, tool: str, error: str, execution_id: str = "", **extra) -> "ToolResult":
        return cls(status="error", tool=tool, error=error, execution_id=execution_id, **extra)

    @classmethod
    def needs_confirmation(cls, tool: str, reason: str = "", **extra) -> "ToolResult":
        return cls(status="confirmation_required", tool=tool, reason=reason, **extra)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "tool": self.tool,
            "execution_id": self.execution_id,
        }
        if self.result is not None:
            out["result"] = self.result
        if self.error is not None:
            out["error"] = self.error
        if self.reason:
            out["reason"] = self.reason
        if self.evidence:
            out["evidence"] = self.evidence
        if self.artifacts:
            out["artifacts"] = self.artifacts
        return out

    def is_success(self) -> bool:
        return self.status == "success"

    def is_failure(self) -> bool:
        return self.status == "error"
