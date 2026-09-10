"""V24 P2: providers routes (moved verbatim from main.py)."""
import logging
import time

import httpx
from compute_manager import snapshot as compute_snapshot
from deps import OLLAMA_URL
from fastapi import APIRouter, HTTPException
from model_lab import benchmark as model_lab_benchmark
from model_lab import hf_search as model_lab_hf_search
from model_lab import lm_load as model_lab_lm_load
from model_lab import lm_unload as model_lab_lm_unload
from model_lab import lmstudio_inventory as model_lab_lmstudio
from model_lab import ollama_inventory as model_lab_ollama
from model_lab import overview as model_lab_overview
from schemas import ConnectionUpdate
from services import (
    CONNECTION_CATALOG,
    _connection_snapshot,
    _load_connection_overrides,
    _save_connection_overrides,
)

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/v1/connections")
async def list_connections():
    return await _connection_snapshot()


@router.post("/v1/connections/{connection_id}/enable")
async def set_connection(connection_id: str, req: ConnectionUpdate):
    if connection_id not in {x["id"] for x in CONNECTION_CATALOG}: raise HTTPException(404,"unknown connection")
    data=_load_connection_overrides(); data[connection_id]={"enabled":req.enabled,"updated_at":time.time()}; _save_connection_overrides(data)
    return await _connection_snapshot()


@router.post("/v1/connections/{connection_id}/test")
async def test_connection(connection_id: str):
    snapshot=await _connection_snapshot(); item=next((x for x in snapshot["connections"] if x["id"]==connection_id),None)
    if not item: raise HTTPException(404,"unknown connection")
    ok=item["status"] in {"connected","configured","installed","ready","available"}
    return {"ok":ok,"connection":item,"tested_at":time.time()}


@router.get("/v1/models")
async def list_models():
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            if response.status_code == 200:
                ollama_models = response.json().get("models", [])
                models = [
                    {
                        "id": model["name"],
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "local"
                    }
                    for model in ollama_models
                ]
                return {"object": "list", "data": models}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch models: {e!s}")

    return {"object": "list", "data": []}


@router.get("/v1/model-lab/overview")
async def model_lab_overview_route():
    return await model_lab_overview()


@router.get("/v1/model-lab/huggingface")
async def model_lab_huggingface(q: str = "gemma 3 4b gguf", limit: int = 8):
    try:
        return await model_lab_hf_search(q, limit)
    except Exception as e:
        raise HTTPException(502, f"Hugging Face search failed: {e}")


@router.post("/v1/model-lab/lmstudio/load")
async def model_lab_load(payload: dict):
    model = str(payload.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "model is required")
    return await model_lab_lm_load(model, int(payload.get("context_length", 4096)), str(payload.get("gpu", "auto")), int(payload.get("ttl", 1800)))


@router.post("/v1/model-lab/benchmark")
async def model_lab_benchmark_route(payload: dict):
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    prompt = str(payload.get("prompt") or "Explain why a 4B quantized model can be faster and more efficient than a 9B model on an 8GB GPU.")
    if not provider or not model:
        raise HTTPException(400, "provider and model are required")
    try:
        return await model_lab_benchmark(provider, model, prompt[:1200])
    except Exception as e:
        raise HTTPException(502, f"Benchmark failed: {e}")


@router.post("/v1/model-lab/lmstudio/unload")
async def model_lab_unload(payload: dict):
    instance_id = str(payload.get("instance_id") or "").strip()
    if not instance_id:
        raise HTTPException(400, "instance_id is required")
    return await model_lab_lm_unload(instance_id)


@router.get("/v1/model-lab/runtimes")
async def model_lab_runtimes():
    return {"ollama": await model_lab_ollama(), "lmstudio": await model_lab_lmstudio(), "compute": await compute_snapshot()}
