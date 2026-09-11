"""Jarvis Model Lab: safe discovery, runtime inventory and benchmarking helpers."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Any

import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LMSTUDIO_URL = os.getenv("LMSTUDIO_URL", "http://localhost:1234")
HF_API = "https://huggingface.co/api/models"

async def _get(client: httpx.AsyncClient, url: str, **kwargs):
    try:
        r = await client.get(url, **kwargs)
        return r
    except Exception:
        return None

async def ollama_inventory() -> dict[str, Any]:
    out = {"online": False, "models": [], "processes": []}
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{OLLAMA_URL}/api/tags")
            if r.status_code == 200:
                out["online"] = True
                out["models"] = r.json().get("models", [])
    except Exception as e:
        out["error"] = str(e)
    try:
        p = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=3)
        out["processes_raw"] = p.stdout.strip()
    except Exception:
        out["processes_raw"] = ""
    return out

async def lmstudio_inventory() -> dict[str, Any]:
    out = {"online": False, "models": []}
    async with httpx.AsyncClient(timeout=4) as c:
        for path in ("/api/v1/models", "/api/v0/models", "/v1/models"):
            r = await _get(c, LMSTUDIO_URL + path)
            if r is not None and r.status_code == 200:
                out["online"] = True
                data = r.json()
                out["models"] = data.get("models") or data.get("data") or []
                out["endpoint"] = path
                break
    return out

async def hf_search(query: str, limit: int = 8) -> dict[str, Any]:
    limit = max(1, min(int(limit), 20))
    params = {"search": query.strip(), "limit": limit, "sort": "downloads", "direction": -1}
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
        r = await c.get(HF_API, params=params)
        r.raise_for_status()
        rows = r.json()
    return {"query": query, "models": [{
        "id": x.get("id"), "downloads": x.get("downloads", 0), "likes": x.get("likes", 0),
        "pipeline": x.get("pipeline_tag"), "library": x.get("library_name"),
        "last_modified": x.get("lastModified")
    } for x in rows]}

async def overview() -> dict[str, Any]:
    ollama, lm = await ollama_inventory(), await lmstudio_inventory()
    return {"timestamp": time.time(), "ollama": ollama, "lmstudio": lm,
            "targets": [
                {"name":"Gemma 2 9B", "query":"gemma2 9b", "role":"fast general", "target_vram_mb":5000},
                {"name":"Llama 3.1 8B", "query":"llama3.1 8b", "role":"tool-capable local default", "target_vram_mb":5000},
                {"name":"Mistral Nemo 12B", "query":"mistral-nemo 12b", "role":"deep reasoning", "target_vram_mb":8000},
            ]}

async def lm_load(model: str, context_length: int = 4096, gpu: str = "auto", ttl: int = 1800) -> dict[str, Any]:
    payload = {"model": model, "context_length": max(1024, min(context_length, 32768))}
    if gpu in {"auto", "max", "off"}: payload["gpu"] = gpu
    if ttl > 0: payload["ttl"] = min(ttl, 86400)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{LMSTUDIO_URL}/api/v1/models/load", json=payload)
        return {"status_code": r.status_code, "ok": r.is_success, "data": r.json() if r.content else {}}

async def lm_unload(instance_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{LMSTUDIO_URL}/api/v1/models/unload", json={"instance_id": instance_id})
        return {"status_code": r.status_code, "ok": r.is_success, "data": r.json() if r.content else {}}


async def benchmark(provider: str, model: str, prompt: str) -> dict[str, Any]:
    provider = provider.strip().lower()
    started = time.perf_counter()
    timeout = httpx.Timeout(75.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as c:
        if provider == "ollama":
            r = await c.post(f"{OLLAMA_URL}/api/chat", json={
                "model": model, "messages": [{"role":"user","content":prompt}],
                "stream": False, "think": False, "options": {"num_predict": 128, "temperature": 0.2}
            })
            data = r.json() if r.content else {}
            answer = (data.get("message") or {}).get("content", "")
            eval_count = data.get("eval_count")
            eval_duration = data.get("eval_duration")
        elif provider == "lmstudio":
            r = await c.post(f"{LMSTUDIO_URL}/v1/chat/completions", json={
                "model": model, "messages": [{"role":"user","content":prompt}],
                "stream": False, "max_tokens": 128, "temperature": 0.2
            })
            data = r.json() if r.content else {}
            answer = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            usage = data.get("usage") or {}
            eval_count = usage.get("completion_tokens")
            eval_duration = None
        else:
            raise ValueError("provider must be ollama or lmstudio")
    elapsed = time.perf_counter() - started
    tok_s = None
    if eval_count and eval_duration:
        tok_s = round(float(eval_count) / (float(eval_duration) / 1e9), 2)
    return {"provider": provider, "model": model, "elapsed_ms": round(elapsed*1000, 1), "tokens": eval_count, "tokens_per_second": tok_s, "answer": answer[:2000]}
