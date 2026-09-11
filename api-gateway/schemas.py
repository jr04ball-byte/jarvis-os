"""V24 P0: request schemas extracted verbatim from main.py.

Single ownership for all FastAPI request models. main.py re-exports
these names for backward compatibility (tests + uvicorn entry).
"""
from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field

# Model defaults shared by schemas and routing (moved from main.py in V24 P0).
FAST_MODEL = os.getenv("JARVIS_FAST_MODEL", "gemma2:9b")
DEEP_MODEL = os.getenv("JARVIS_DEEP_MODEL", "mistral-nemo:12b")
TOOL_MODEL = os.getenv("JARVIS_TOOL_MODEL", "llama3.1:8b")

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "auto"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = 0.7
    max_tokens: int | None = 1024
    conversation_id: int | None = None
    use_rag: bool = False
    assistant_profile: str = "general"


class ConversationCreate(BaseModel):
    title: str
    model: str = "auto"
    assistant_profile: str = "general"


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class CompareRequest(BaseModel):
    prompt: str
    models: list[str] = Field(default_factory=lambda: [FAST_MODEL, DEEP_MODEL])


class DocumentUpload(BaseModel):
    doc_id: str
    content: str


class ConnectionUpdate(BaseModel):
    enabled: bool


class GeminiChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None
    temperature: float | None = 0.7
    max_tokens: int | None = 1024


class GeminiLiveTokenRequest(BaseModel):
    ttl_minutes: int | None = 10


class OpenCodeTaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    path: str | None = None
    target: str | None = None
    mode: Literal["inspect", "build", "fix", "refactor"] = "inspect"


class DeepgramSpeakRequest(BaseModel):
    text: str
    model: str | None = None
    speed: float | None = 1.0


class ArtifactRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="markdown", max_length=40)
    content: str = Field(default="", max_length=1_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=1_000_000)
    metadata: dict[str, Any] | None = None


class ResearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    num_results: int = Field(default=5, ge=1, le=10)


class ToolRequest(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    confirmed: bool = False


class OrchestratorGoal(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)


class OrchestratorTransition(BaseModel):
    task_id: str
    status: str
    result: Any | None = None
    error: str | None = None


class ProjectWorkerTargetRequest(BaseModel):
    target: str


class ProjectWorkerVerifyRequest(BaseModel):
    target: str
    run_tests: bool = True
    run_build: bool = True
    run_lint: bool = False


class ProjectWorkerImplementRequest(BaseModel):
    target: str
    goal: str = Field(min_length=1, max_length=12000)
    verify: bool = True


class ProjectWorkerAutofixRequest(BaseModel):
    target: str
    goal: str = Field(min_length=1, max_length=12000)
    max_retries: int = Field(default=2, ge=0, le=4)
    run_lint: bool = False
    resume_run_id: str | None = None


class AgentChatRequest(BaseModel):
    model: str = "auto"
    messages: list[ChatMessage]
    assistant_profile: str = "general"
    conversation_id: int | None = None
    max_tool_rounds: int = 5


class OrchestratorRunRequest(BaseModel):
    project_id: str
    max_steps: int = Field(default=12, ge=1, le=30)
    model: str = "auto"
    assistant_profile: str = "general"


class OrchestratorAutopilotRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    max_steps: int = Field(default=12, ge=1, le=30)
    model: str = "auto"
    assistant_profile: str = "general"


class VoiceTurnRequest(BaseModel):
    model: str = "auto"
    messages: list[ChatMessage]
    conversation_id: int | None = None
    assistant_profile: str = "general"
    temperature: float | None = 0.7
    max_tokens: int | None = 512


class ConfirmationRequest(BaseModel):
    confirmation_id: str = Field(min_length=20, max_length=128)
    confirmed: bool = False
