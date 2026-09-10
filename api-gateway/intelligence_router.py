from __future__ import annotations

import os
import re
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

from providers import ProviderAdapter


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


@dataclass
class TaskAssessment:
    mode: str
    complexity: int
    privacy: int
    coding: int
    speed_priority: int
    cost_sensitivity: int
    risk: int
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RouteDecision:
    requested: str
    selected: str
    chain: List[str]
    assessment: TaskAssessment
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "requested": self.requested,
            "selected": self.selected,
            "chain": list(self.chain),
            "assessment": self.assessment.as_dict(),
            "reason": self.reason,
        }


class RouterTelemetry:
    def __init__(self, max_events: int = 200) -> None:
        self.events = deque(maxlen=max_events)
        self.counts = Counter()
        self.failures = Counter()

    def record(self, provider: str, *, ok: bool, elapsed_ms: int, route_reason: str) -> None:
        event = {
            "provider": provider,
            "ok": bool(ok),
            "elapsed_ms": int(elapsed_ms),
            "route_reason": route_reason,
            "ts": time.time(),
        }
        self.events.append(event)
        self.counts[provider] += 1
        if not ok:
            self.failures[provider] += 1

    def snapshot(self) -> Dict[str, Any]:
        return {
            "calls": dict(self.counts),
            "failures": dict(self.failures),
            "recent": list(self.events)[-20:],
        }


class ProviderCircuitBreaker:
    """Small in-process circuit breaker so one unhealthy provider cannot stall every request."""

    def __init__(self, threshold: int = 3, cooldown_seconds: int = 30) -> None:
        self.threshold = max(1, int(threshold))
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self._failures: Dict[str, int] = {}
        self._opened_until: Dict[str, float] = {}

    def record(self, provider: str, ok: bool) -> None:
        now = time.time()
        if ok:
            self._failures[provider] = 0
            self._opened_until.pop(provider, None)
            return
        failures = self._failures.get(provider, 0) + 1
        self._failures[provider] = failures
        if failures >= self.threshold:
            self._opened_until[provider] = now + self.cooldown_seconds

    def is_open(self, provider: str) -> bool:
        until = self._opened_until.get(provider, 0.0)
        if not until:
            return False
        if time.time() >= until:
            self._opened_until.pop(provider, None)
            self._failures[provider] = 0
            return False
        return True

    def filter(self, chain: Iterable[str]) -> List[str]:
        return [name for name in chain if not self.is_open(name)]

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        names = set(self._failures) | set(self._opened_until)
        return {
            name: {
                "state": "open" if self.is_open(name) else "closed",
                "consecutive_failures": self._failures.get(name, 0),
                "retry_in_seconds": max(0, int(self._opened_until.get(name, 0) - now)),
            }
            for name in sorted(names)
        }


class IntelligenceRouter:
    """Jarvis V21 routing policy.

    Gemini is the default main brain. OpenAI is the deep-reasoning escalation
    provider. Ollama is the private/local/low-cost provider. OpenCode is a
    bounded software-engineering worker. Jarvis, not any provider, owns
    permissions, project state, memory and verification.
    """

    _CODING = re.compile(
        r"\b(implement|fix|debug|refactor|repository|repo|codebase|patch|commit|"
        r"write code|edit code|modify code|unit test|integration test|build failure|"
        r"typescript|javascript|python|powershell|dockerfile|migration)\b",
        re.I,
    )
    _DEEP = re.compile(
        r"\b(architect|architecture|distributed|consensus|raft|paxos|threat model|"
        r"security review|root cause|complex|deep analysis|production ready|performance|"
        r"race condition|idempotenc|database design|system design|multi-agent)\b",
        re.I,
    )
    _PRIVATE = re.compile(
        r"\b(private|offline|local only|local-only|do not send|sensitive|confidential|secret)\b",
        re.I,
    )
    _FAST = re.compile(r"\b(quick|fast|brief|simple|summarize|classify|extract)\b", re.I)
    _RISK = re.compile(
        r"\b(delete|drop database|deploy|production|credential|secret|payment|purchase|"
        r"send email|make call|shell|powershell|administrator|admin|registry|format disk)\b",
        re.I,
    )

    MODES = {"auto", "fast", "normal", "deep", "private", "coding", "autopilot"}

    def __init__(self, providers: Dict[str, ProviderAdapter]) -> None:
        self.providers = providers
        self.primary = _env("JARVIS_PRIMARY_BRAIN", "gemini").lower()
        self.deep_provider = _env("JARVIS_DEEP_BRAIN", "openai").lower()
        self.local_provider = _env("JARVIS_LOCAL_BRAIN", "ollama").lower()
        self.coding_provider = _env("JARVIS_CODING_BRAIN", "opencode").lower()
        self.telemetry = RouterTelemetry()
        self.circuit = ProviderCircuitBreaker(
            threshold=int(_env("JARVIS_CIRCUIT_FAILURES", "3") or 3),
            cooldown_seconds=int(_env("JARVIS_CIRCUIT_COOLDOWN_SECONDS", "30") or 30),
        )

    @staticmethod
    def _clamp(value: int) -> int:
        return max(0, min(int(value), 10))

    def assess(self, text: str, mode: str = "auto", profile: str = "general") -> TaskAssessment:
        text = text or ""
        mode = (mode or "auto").strip().lower()
        if mode not in self.MODES and mode not in self.providers:
            mode = "auto"

        coding = 8 if self._CODING.search(text) else 1
        complexity = 8 if self._DEEP.search(text) else (6 if coding >= 8 else 3)
        privacy = 9 if self._PRIVATE.search(text) else 2
        speed = 8 if self._FAST.search(text) else 4
        cost = 8 if mode == "fast" else 4
        risk = 8 if self._RISK.search(text) else 2

        if profile == "sales":
            complexity = min(complexity, 5)
        if mode == "private":
            privacy = 10
        if mode == "deep":
            complexity = max(complexity, 9)
        if mode == "coding":
            coding = 10
        if mode == "fast":
            speed, cost = 10, 9
        if mode == "autopilot":
            complexity = max(complexity, 7)
            risk = max(risk, 5)

        reason = "default general task"
        if privacy >= 8:
            reason = "privacy/local-only requirement"
        elif coding >= 8:
            reason = "software-engineering task"
        elif complexity >= 8:
            reason = "high-complexity reasoning task"
        elif speed >= 8:
            reason = "latency/cost-sensitive task"

        return TaskAssessment(
            mode=mode,
            complexity=self._clamp(complexity),
            privacy=self._clamp(privacy),
            coding=self._clamp(coding),
            speed_priority=self._clamp(speed),
            cost_sensitivity=self._clamp(cost),
            risk=self._clamp(risk),
            reason=reason,
        )

    def _unique_existing(self, names: Iterable[str]) -> List[str]:
        out: List[str] = []
        for name in names:
            name = (name or "").lower()
            if name in self.providers and name not in out:
                out.append(name)
        return out

    def decide(self, text: str, requested: str = "auto", profile: str = "general") -> RouteDecision:
        req = (requested or "auto").strip().lower()
        assessment = self.assess(text, req, profile)

        if req in self.providers:
            chain = self._unique_existing([req, self.primary, self.deep_provider, self.local_provider])
            return RouteDecision(req, chain[0], chain, assessment, f"explicit provider request: {req}")

        if assessment.mode == "private":
            chain = self._unique_existing([self.local_provider])
            reason = "private mode enforces local-only execution"
        elif assessment.mode == "coding" or assessment.coding >= 8:
            chain = self._unique_existing([self.coding_provider, self.deep_provider, self.primary, self.local_provider])
            reason = "coding task routed to bounded coding worker with cloud/local fallbacks"
        elif assessment.mode == "deep" or assessment.complexity >= 8:
            chain = self._unique_existing([self.deep_provider, self.primary, self.local_provider])
            reason = "deep task escalated to specialist reasoning provider"
        elif assessment.mode == "fast":
            chain = self._unique_existing([self.local_provider, self.primary, self.deep_provider])
            reason = "fast mode prefers local low-latency execution"
        else:
            chain = self._unique_existing([self.primary, self.local_provider, self.deep_provider])
            reason = "normal task stays on Gemini main brain"

        if not chain:
            raise RuntimeError("no intelligence providers registered")
        return RouteDecision(req, chain[0], chain, assessment, reason)

    def provider_context(self, decision: RouteDecision, **extra: Any) -> Dict[str, Any]:
        context = decision.assessment.as_dict()
        context.update(extra)
        return context

    def eligible_chain(self, chain: Iterable[str]) -> List[str]:
        return self.circuit.filter(chain)

    def record_provider_result(self, provider: str, *, ok: bool, elapsed_ms: int, route_reason: str) -> None:
        self.telemetry.record(provider, ok=ok, elapsed_ms=elapsed_ms, route_reason=route_reason)
        self.circuit.record(provider, ok)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "policy": {
                "primary": self.primary,
                "deep": self.deep_provider,
                "local": self.local_provider,
                "coding": self.coding_provider,
                "modes": sorted(self.MODES),
                "authority": "Jarvis owns memory, permissions, execution and verification; providers only supply intelligence/work product.",
            },
            "providers": {name: provider.describe() for name, provider in self.providers.items()},
            "telemetry": self.telemetry.snapshot(),
            "circuits": self.circuit.snapshot(),
        }
