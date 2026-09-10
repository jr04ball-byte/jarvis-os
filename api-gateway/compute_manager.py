"""Adaptive compute and VRAM management for Jarvis.

GPU-first by default. Falls back to CPU when the GPU is under pressure. Hybrid
mode is exposed as an explicit option for future larger models.
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Any

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
COMPUTE_MODE = os.getenv("JARVIS_COMPUTE_MODE", "auto").strip().lower()
MIN_FREE_VRAM_MB = int(os.getenv("JARVIS_MIN_FREE_VRAM_MB", "768"))
HYBRID_GPU_LAYERS = int(os.getenv("JARVIS_HYBRID_GPU_LAYERS", "20"))


def gpu_stats() -> dict[str, Any]:
    """Return NVIDIA GPU memory/utilization stats without requiring nvidia Python packages."""
    base = {"available": False, "name": None, "memory_used_mb": None, "memory_total_mb": None,
            "memory_free_mb": None, "utilization_gpu": None, "temperature_c": None}
    try:
        p = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=True,
        )
        line = next((x.strip() for x in p.stdout.splitlines() if x.strip()), "")
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 5:
            used, total = int(parts[1]), int(parts[2])
            base.update({"available": True, "name": parts[0], "memory_used_mb": used,
                         "memory_total_mb": total, "memory_free_mb": max(0, total-used),
                         "utilization_gpu": int(parts[3]), "temperature_c": int(parts[4])})
    except Exception:
        pass
    return base


def ollama_processes() -> list[dict[str, Any]]:
    try:
        p = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=3)
        rows = [x.strip() for x in p.stdout.splitlines() if x.strip()]
        if len(rows) < 2:
            return []
        out = []
        for row in rows[1:]:
            cols = row.split()
            if len(cols) >= 5:
                out.append({"model": cols[0], "size": " ".join(cols[1:3]), "processor": " ".join(cols[3:-2]), "context": cols[-2], "until": cols[-1]})
        return out
    except Exception:
        return []


def select_mode(requested: str | None = None) -> dict[str, Any]:
    requested = (requested or COMPUTE_MODE).strip().lower()
    if requested not in {"auto", "gpu", "cpu", "hybrid"}:
        requested = "auto"
    stats = gpu_stats()
    mode = requested
    reason = "manual mode"
    if requested == "auto":
        if stats["available"] and (stats["memory_free_mb"] or 0) >= MIN_FREE_VRAM_MB:
            mode, reason = "gpu", "GPU has sufficient free VRAM"
        elif not stats["available"]:
            mode, reason = "cpu", "NVIDIA GPU is unavailable; CPU inference is the safe fallback"
        else:
            # Do not silently switch a loaded GPU model to CPU: Ollama may keep
            # the GPU copy resident, which can temporarily consume *more* RAM/VRAM.
            # Auto mode therefore protects the GPU and asks the caller to free it
            # or explicitly select CPU mode.
            mode, reason = "gpu_guarded", "GPU VRAM is below the safety threshold; keep the active model stable and avoid a duplicate CPU copy"
    options: dict[str, Any] = {}
    if mode == "cpu":
        options["num_gpu"] = 0
    elif mode == "hybrid":
        options["num_gpu"] = max(1, HYBRID_GPU_LAYERS)
    return {"requested": requested, "mode": mode, "reason": reason, "options": options, "gpu": stats,
            "ollama_processes": ollama_processes()}


async def snapshot() -> dict[str, Any]:
    selected = select_mode()
    selected["ollama_url"] = OLLAMA_URL
    selected["default_model"] = OLLAMA_MODEL
    selected["timestamp"] = time.time()
    return selected
