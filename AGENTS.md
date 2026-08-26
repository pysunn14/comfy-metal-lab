# Comfy Metal Lab — Agent Guide

## Goal

Run reproducible ComfyUI inference benchmarks on Apple Silicon. A successful generation is not enough: the benchmark must be methodologically valid and leave inspectable evidence.

## Core concepts

- **Runtime**: the external ComfyUI checkout plus machine-specific launch configuration, model paths, Python environment, and available custom nodes.
- **Workload**: what is generated: API workflow, runtime requirements, cold/warm mutations, output selection, and generation settings.
- **Profile**: the experimental execution delta being compared, such as an attention backend or precision option. Keep machine-specific paths out of profiles.

Keep both the workload and runtime identical when comparing profiles.

## Benchmark rules

- Use a deterministic workload when measuring performance.
- Validate workload node requirements against the selected runtime before expensive generation begins.
- Keep the same runtime configuration when comparing profiles; a profile should represent the experiment variable, not a workload-specific environment bundle.
- Keep the declared cold/warm workflow mutations and all other generation settings fixed across compared profiles.
- Use a fresh worker process and a fresh ComfyUI server for every session.
- In each session, measure one model-cold prompt followed by one model-warm prompt in the same ComfyUI process.
- Keep the complete session contract, including output selection, identical across compared profiles.
- Preserve every raw timing; report separate medians for server startup, cold generation, warm generation, and time to first image.
- Use warm-generation speedup as the primary metric for optimizations that affect in-model execution.
- The first real session may serve as the runtime probe; do not add a separate expensive image-generation canary by default.
- Do not change the workload or silently relax validation just to make a run pass.
- Do not report a speedup as valid when the run or quality checks are invalid.

## Safety

- Treat ComfyUI as an external runtime; do not vendor or fork it as part of routine benchmark setup.
- Do not use `sudo` or change macOS system limits automatically.
- Do not kill unrelated ComfyUI processes.
- Stop and report abnormal OOM, swap, thermal/performance, or runtime failures instead of blindly continuing.
- Do not describe process RSS as Metal/MPS allocator memory. Keep memory metrics distinct.
- Never overwrite an existing benchmark result directory.

## Artifacts

A completed run should preserve the evidence produced by the harness, including:

- `report.json`
- generated image artifacts
- worker stdout/stderr
- environment or telemetry artifacts when the harness records them

Treat structured run artifacts as the source of truth, not a console success message alone.

## Repository boundaries

- Public source code must not depend on personal model names, private prompts, private characters, or machine-specific private assets.
- Local/private workloads, runtimes, profiles, reference repositories, and experiment outputs belong under `_local/`.
- Never commit `_local/`.

## Development

Run the project tests with:

```bash
uv run python -m pytest tests
```

Keep changes focused. Read the existing implementation before adding new abstraction or documentation; prefer code as the source of implementation detail and this file as the source of benchmark policy.
