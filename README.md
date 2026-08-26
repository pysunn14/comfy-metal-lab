# Comfy Metal Lab

> Reproducible ComfyUI inference benchmarking and optimization for Apple Silicon.

Comfy Metal Lab is an open-source experiment harness for benchmarking ComfyUI image-generation workloads on Apple Silicon and comparing MPS/Metal inference optimizations under controlled conditions.

ComfyUI is treated as an **external runtime**, not vendored or forked by this project.

- **Runtime** — the ComfyUI checkout plus machine-specific Python, model paths, and available custom nodes
- **Workload** — the API workflow, runtime requirements, cold/warm mutations, output selection, and generation conditions
- **Profile** — the experimental execution delta being compared, such as an attention backend or precision option

The project is currently in early development. The benchmark protocol uses isolated sessions: each fresh ComfyUI process measures one model-cold generation followed by one model-warm generation, with wall-clock timing, MPS memory metrics, machine-readable reports, and image-quality regression checks.

## Start with an Agent

Using a coding agent? Ask it to read [`AGENTS.md`](AGENTS.md) first, then describe the ComfyUI workload or comparison you want to run.

> Read `AGENTS.md` first, then benchmark my ComfyUI workload using the repository protocol.


## Runtime compatibility

Machine-specific launch configuration belongs in a runtime config, while profiles contain only the experiment delta. API-workflow `class_type` values are treated as implicit runtime requirements.

```toml
# runtime.toml
name = "local-comfyui"
base_directory = "/path/to/comfyui-model-base"
```

```toml
# metal-flash.toml
name = "metal-flash"
server_args = ["--use-flash-attention"]
```

Use `comfy-metal preflight --comfyui-root ... --workload ... --runtime ... --profile ...` to start ComfyUI, verify required nodes through `/object_info`, and exit without generating an image. Benchmarks keep the workload and runtime fixed while changing profiles.

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

For common API-format workflows, `comfy-metal inspect workflow_api.json` detects seed/noise inputs and `SaveImage` outputs. Add `--write workload.toml` when the inspection is unambiguous. Nested JSON settings are supported through `path` + `format = "json"`.

Use `[[overrides]]` for workload-static variants that should be applied before both cold and warm prompts:

```toml
[[overrides]]
node = "12"
input = "enabled"
value = false
```

Static overrides and session mutations are separate contracts and cannot target the same workflow input/path.

## Documentation

- [한국어 README](README.ko.md)

## License

Apache License 2.0. See [LICENSE](LICENSE).
