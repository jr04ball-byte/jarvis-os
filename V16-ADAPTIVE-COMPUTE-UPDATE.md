# Jarvis V16 — Adaptive Compute Core

V16 keeps Qwen 3.5 9B as the primary local brain and adds GPU-first adaptive compute.

## Compute modes
- `auto` (default): use GPU when enough VRAM is available; use CPU only when NVIDIA is unavailable; protect the active model when VRAM is low rather than silently creating a duplicate CPU copy.
- `gpu`: force normal GPU inference.
- `cpu`: Ollama `num_gpu=0`, using system RAM/CPU.
- `hybrid`: Ollama `num_gpu=<JARVIS_HYBRID_GPU_LAYERS>`.

## Resource endpoint
`GET /v1/system/compute` reports NVIDIA memory/utilization, Ollama processes, selected mode, safety reason, and configured model.

## Context protection
Conversation history defaults to 24 messages and a 14,000-character context budget. RAG augmentation is capped at 6,000 characters.

## Safety
Auto mode deliberately does not switch a loaded model from GPU to CPU when VRAM is low because that can create a second resident copy and make memory pressure worse. Explicit CPU mode remains available.

## Qwen
The agent loop explicitly sends `think:false`, matching the normal chat path.
