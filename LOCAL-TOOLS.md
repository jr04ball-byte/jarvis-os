# Local Tools / Plugin Architecture

The AI System is **capability-first**: the user can request an outcome such as
"create me a decent video using the tools on this PC" without naming Blender,
Unreal Engine, ComfyUI, or FFmpeg.

## Current phase

The gateway now scans Windows for these local tools:

- Blender — scene creation, camera, lighting, animation, render, export
- Unreal Engine 5 — projects, levels, assets, Sequencer, Movie Render Queue, render/export
- ComfyUI — image generation and workflow execution
- FFmpeg — video/audio assembly and transcoding
- Ollama — local LLM runtime
- Python — local automation runtime

Discovery is read-only. **Finding an executable does not grant the model arbitrary
process execution.** Each future plugin will expose a narrow, audited capability
set and confirmation gates where appropriate.

## Planned plugin flow

1. Scan and register local tools.
2. AI reads the capability registry.
3. AI creates a workflow/DAG from the requested outcome.
4. Each plugin validates its inputs and allowed paths.
5. Sensitive or destructive steps require approval.
6. Outputs are written to an allowed workspace and returned to the user.

This lets Blender and Unreal complement each other rather than forcing either one
to be the default. For example, a video workflow can use ComfyUI for assets,
Blender or Unreal for 3D scenes, and FFmpeg for deterministic final assembly.

## Voice speech-model compatibility fix

Browser Whisper is pinned to WASM/CPU with explicit `fp32` weights. This avoids the quantized ONNX decoder path that can fail with errors such as `TransposeDQWeightsForMatMulNBits` / missing `*_scale` tensors in some browser ONNX Runtime combinations.
