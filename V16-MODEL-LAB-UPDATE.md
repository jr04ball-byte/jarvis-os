# Jarvis V16 — Model Lab

Model Lab adds a controlled model-selection layer without replacing Ollama.

## Runtime roles
- Ollama = production local inference.
- LM Studio = experimental/benchmark runtime when its local server is enabled on port 1234.
- Hugging Face = discovery/search only until a model is explicitly approved.

## Dashboard
The Model Lab appears inside the main dashboard and reports:
- Ollama availability and installed models
- LM Studio availability and models
- RTX GPU/free VRAM snapshot
- Hugging Face search results
- per-model benchmark action for Ollama

## Safety
Model Lab does not auto-download arbitrary Hugging Face models. It is intentionally discovery-first.

LM Studio load/unload endpoints are available through the gateway, but Jarvis should only use them after a user-approved model is selected. Keep one GPU-heavy model active on the RTX 3070 to avoid VRAM collisions with Blender/ComfyUI.

## Current benchmark
Benchmark requests use a short prompt and capped generation length. Ollama requests explicitly disable Qwen thinking output (`think:false`) so the benchmark measures visible answer generation.
