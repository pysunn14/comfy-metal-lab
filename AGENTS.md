# Comfy Metal Lab — Agent Guide

## Goal

Run reproducible ComfyUI inference benchmarks on Apple Silicon. A successful generation is not enough: the benchmark must be methodologically valid and leave inspectable evidence.

## Core concepts

- **Runtime**: the external ComfyUI checkout plus machine-specific launch configuration, model paths, Python environment, and available custom nodes.
- **Workload**: what is generated: API workflow, runtime requirements, cold/warm mutations, output selection, and generation settings.
- **Profile**: the experimental execution delta being compared, such as an attention backend or precision option. Keep machine-specific paths out of profiles.

Keep both the workload and runtime identical when comparing profiles.

## Default agent workflow

Prefer the managed workspace flow unless the user explicitly provides a manual layout:

1. Read this file before changing code or running a benchmark.
2. Identify the benchmark target from the user's request. If the user has not explicitly named or supplied a workflow/workload, ask which one to use. Do not infer a benchmark target from local files.
3. If `.comfy-metal/` is missing, run `comfy-metal init`.
4. If an explicitly selected workflow is outside the managed workspace, import it with `comfy-metal import-workload <workflow> --name <name>`. Do not copy private workflows into public repository paths. If automatic import is ambiguous, use `comfy-metal inspect <workflow>` and create/edit the managed manifest with explicit mutation/output targets instead of guessing.
5. Run `comfy-metal doctor --comfyui-root <ComfyUI>` with the selected runtime/profile. Treat `BLOCKED` as a stop condition; surface `WARN` evidence before interpreting small speedups.
6. Run `comfy-metal preflight` for the selected workload/runtime/profile before expensive generation.
7. Run `comfy-metal bench` only when the user has explicitly asked for a benchmark or measurement. If the request is only setup/readiness validation, stop after doctor/preflight and report the result.
8. Compare only runs whose workload, runtime, and benchmark contract satisfy the comparison invariants, and only when comparison is part of the user's request.
9. Report the raw/median timing evidence, quality result, relevant warnings, and artifact locations for any benchmark that was actually run.

Do not ask the user to manually organize files that `init` or `import-workload` can manage. Do not generate an image during `doctor` or `preflight`.

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
- User-managed local workloads, runtimes, profiles, and experiment outputs belong under `.comfy-metal/`; keep that workspace private and uncommitted.
- Developer-only legacy/reference material may remain under `_local/`. Never commit `.comfy-metal/` or `_local/`.

## Development

Run the project tests with:

```bash
uv run python -m pytest tests
```

Keep changes focused. Read the existing implementation before adding new abstraction or documentation; prefer code as the source of implementation detail and this file as the source of benchmark policy.

For user-facing README changes, treat `README.md` as the canonical behavior/usage document. Update `README.md` first, then update `README.ko.md` in the same task and commit. Keep commands, feature availability, and workflow semantics aligned between the two; the Korean version should be a natural equivalent, not necessarily a literal translation.
