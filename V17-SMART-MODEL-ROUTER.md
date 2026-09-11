# V17 Smart Model Router

V17 uses measured local hardware results to route automatically:

- `gemma3:4b` = fast/default model for everyday, voice, and sales requests.
- `llama3.1:8b` = deep model for coding, architecture, analysis, math, research, and other complexity hints.
- Explicit model names still override routing.
- `OLLAMA_MODEL=auto` is the default.
- `JARVIS_FAST_MODEL` and `JARVIS_DEEP_MODEL` are configurable.
- The router does not keep both models resident intentionally; Ollama can evict the previous model as needed.

Live benchmark basis from the user's RTX 3070 8GB: Gemma 3 4B measured ~109.9 tok/s generation and ~2.9 GB resident; Qwen 3.5 9B measured ~63.47 tok/s and ~5.5 GB resident. These measurements establish speed/VRAM differences, not a broad intelligence ranking.

## V17.1 tool-calling compatibility fix
Gemma 3 4B is retained as the fast chat model, but Ollama reports that `gemma3:4b` does not support tools. The agent endpoint now routes automatic tool-calling requests to `JARVIS_TOOL_MODEL` (default `llama3.1:8b`). If a caller explicitly selects the fast Gemma model for the agent, Jarvis safely falls back to the tool-capable model instead of sending an unsupported `tools` payload to Gemma.

