# Comfy Metal Lab

> Reproducible ComfyUI inference benchmarking and optimization for Apple Silicon.

[한국어 README](README.ko.md)

Comfy Metal Lab is an open-source experiment harness for benchmarking ComfyUI image-generation workloads on Apple Silicon and comparing MPS/Metal inference optimizations under controlled conditions.

ComfyUI is treated as an **external runtime**, not vendored or forked by this project.

- **Runtime** — the ComfyUI checkout plus machine-specific Python, model paths, and available custom nodes
- **Workload** — the API workflow, runtime requirements, cold/warm mutations, output selection, and generation conditions
- **Profile** — the experimental execution delta being compared, such as an attention backend or precision option

The project is currently in early development. The benchmark protocol uses isolated sessions: each fresh ComfyUI process measures one model-cold generation followed by one model-warm generation, with wall-clock timing, MPS memory metrics, machine-readable reports, and image-quality regression checks.

## Start with an Agent

Using a coding agent? Ask it to read [`AGENTS.md`](AGENTS.md) first. Give it the workflow/workload you actually want to use; if the benchmark target is ambiguous, the agent should ask rather than infer one from local files. Doctor and preflight may be used to validate readiness, but generation should only start when you explicitly request a benchmark.

> Read `AGENTS.md` first and inspect the managed workspace/runtime. If I have not specified the exact workflow or workload to benchmark, ask me which one to use. Once the target is explicit, prepare it and run doctor/preflight; only run the benchmark when I explicitly ask for a measurement.

## Install

Install the CLI once with `uv`; after that, `comfy-metal` is available directly from your shell:

```bash
uv tool install git+https://github.com/pysunn14/comfy-metal-lab.git
```

## Quickstart

Initialize a project-local managed workspace, then import an API-format ComfyUI workflow from anywhere on disk:

```bash
comfy-metal init
comfy-metal doctor
comfy-metal import-workload ~/Downloads/workflow_api.json --name my-workload
```

This creates a private local workspace under `.comfy-metal/` with managed workloads, runtimes, profiles, and results. The default `local` runtime and `stock` profile are created automatically. When an obvious adjacent `ComfyUI` checkout is found, `init` records it as `comfyui_root` in the default runtime so later commands do not need the path repeated.

`comfy-metal doctor` performs a real readiness check: it probes the selected ComfyUI Python/PyTorch/MPS runtime, validates runtime paths, records machine state, detects competing ComfyUI/benchmark processes, starts ComfyUI with the selected runtime/profile, checks `/system_stats`, and verifies the MPS telemetry wrapper. It reports `READY`, `WARN`, or `BLOCKED` without generating an image.

Managed configs can be referenced by name:

```bash
comfy-metal preflight \
  --workload my-workload \
  --runtime local \
  --profile stock

comfy-metal bench \
  --workload my-workload \
  --runtime local \
  --profile stock \
  --sessions 3
```

When `--output-dir` is omitted, results are allocated automatically under `.comfy-metal/results/`. Explicit paths remain supported for advanced/manual setups.

## Runtime compatibility

Machine-specific launch configuration belongs in a runtime config, while profiles contain only the experiment delta. API-workflow `class_type` values are treated as implicit runtime requirements.

```toml
# runtime.toml
name = "local-comfyui"
comfyui_root = "/path/to/ComfyUI"
base_directory = "/path/to/comfyui-model-base"
```

```toml
# metal-flash.toml
name = "metal-flash"
server_args = ["--use-flash-attention"]
```

Use `comfy-metal preflight --workload ... --runtime ... --profile ...` to start ComfyUI, verify required nodes through `/object_info`, and exit without generating an image. `--comfyui-root` remains available as an explicit override when needed. Benchmarks keep the workload and runtime fixed while changing profiles.

## Workload manifests

The benchmark core does not need model-specific seed logic. A workload declares exactly which API-workflow input changes between cold and warm prompts and which output node is the benchmark artifact.

```toml
name = "example"
workflow = "workflow_api.json"

[[session.mutations]]
node = "7"
input = "seed"
cold = 42
warm = 43

[output]
node = "9"
index = 0
```

For common API-format workflows, `comfy-metal import-workload workflow_api.json` copies the workflow into the managed workspace and generates a manifest when seed/noise and `SaveImage` targets are unambiguous. `comfy-metal inspect` remains available as a read-only/manual inspection tool. Nested JSON settings are supported through `path` + `format = "json"`.

Use `[[overrides]]` for workload-static variants that should be applied before both cold and warm prompts:

```toml
[[overrides]]
node = "12"
input = "enabled"
value = false
```

Static overrides and session mutations are separate contracts and cannot target the same workflow input/path.

## Comparisons

`comfy-metal compare` remains the strict profile-comparison path: workload and runtime must match, and SSIM acts as a correctness gate.

For broader experiments, `comfy-metal compare-contract` uses an explicit `vary` contract. Factors listed in `vary` may differ; undeclared factors must still match.

```toml
name = "base-vs-turbo"
vary = ["workload"]
```

When `workload` varies, SSIM is descriptive rather than a pass/fail gate. Multi-factor contracts such as `vary = ["workload", "profile"]` measure the combined stack effect and should not be attributed to one factor alone.

## License

Apache License 2.0. See [LICENSE](LICENSE).
