# Comfy Metal Lab

> Reproducible ComfyUI inference benchmarking and optimization for Apple Silicon.

Comfy Metal Lab is an open-source experiment harness for benchmarking ComfyUI image-generation workloads on Apple Silicon and comparing MPS/Metal inference optimizations under controlled conditions.

ComfyUI is treated as an **external runtime**, not vendored or forked by this project.

- **Runtime** — ComfyUI, PyTorch, and the MPS environment
- **Profile** — precision, attention backend, startup options, and other execution settings
- **Workload** — workflow, model requirements, resolution, sampler, seed, and other generation conditions

The project is currently in early development. The first milestone is a trustworthy baseline benchmark with isolated repetitions, cold/warm measurements, wall-clock timing, MPS memory metrics, machine-readable reports, and image-quality regression checks.

## Documentation

- [한국어 README](README.ko.md)

## License

Apache License 2.0. See [LICENSE](LICENSE).
