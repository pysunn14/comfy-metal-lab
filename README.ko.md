# Comfy Metal Lab

> Apple Silicon에서 ComfyUI 추론을 재현 가능하게 벤치마크하고 최적화하기 위한 오픈소스 실험 하네스

Comfy Metal Lab은 Apple Silicon 환경에서 ComfyUI 이미지 생성 워크로드의 성능을 측정하고, MPS/Metal 기반 최적화를 동일한 조건에서 비교하기 위한 프로젝트입니다.

ComfyUI 자체를 포크하지 않고 **외부 runtime**으로 사용하며, 벤치마크 설정과 workload를 별도로 관리합니다.

## 핵심 개념

Comfy Metal Lab은 실행 환경을 세 가지로 나눕니다.

- **Runtime** — ComfyUI checkout, Python/PyTorch/MPS, 모델 경로, 설치된 custom node 등 머신별 실행 환경
- **Workload** — API workflow, 필요한 node, cold/warm mutation, output 등 실행 대상
- **Profile** — attention backend나 precision처럼 비교하려는 실험 변수

> **Workload는 무엇을 실행하는지, Runtime은 그것을 실행할 수 있는 환경인지, Profile은 어떤 실험 변수를 적용할지 정의합니다.**

성능 비교에서는 workload와 runtime을 고정하고 profile만 바꿉니다.

## Start with an Agent

코딩 에이전트에게 전담하려면 먼저 [`AGENTS.md`](AGENTS.md)를 읽게 한 뒤, 실행하거나 비교할 ComfyUI workload를 설명하면 됩니다.

> `AGENTS.md`를 먼저 읽고 저장소의 benchmark protocol에 따라 내 ComfyUI workload를 벤치마크해줘.

## 목표

- Apple Silicon에서 재현 가능한 ComfyUI benchmark
- cold / warm inference 측정
- wall-clock latency 및 MPS memory 기록
- optimization별 성능 비교
- machine-readable JSON report
- 이미지 품질 regression check
- 공개 example workload 제공

## 구조

```text
Workload ── requires ──> Runtime
   │                     │
   └──── benchmark ──────┤
                         │
                 ┌───────┴───────┐
                 │               │
              stock          metal-flash
               Profile          Profile
```

ComfyUI는 이 저장소에 vendoring하거나 fork하지 않습니다.

## 벤치마크 원칙

단순히 가장 빠른 실행 시간을 기록하는 것이 아니라, **재현 가능하고 설명 가능한 성능 차이**를 측정하는 것을 목표로 합니다.

초기 benchmark harness는 다음 원칙을 따릅니다.

- session 단위 프로세스 격리
- 같은 session 안에서 model-cold / model-warm 생성 분리
- 동일 workload 조건 유지
- wall-clock + memory 기록
- 가능한 경우 mechanism-level counter 기록
- 품질 regression 검사

초기 방법론은 `mlx-teacache`의 benchmark design에서 일부 아이디어를 참고하되, 이를 ComfyUI + PyTorch MPS 환경에 맞게 적용합니다.

## 범위

초기 개발 범위:

- Apple Silicon Mac
- macOS
- ComfyUI
- PyTorch MPS
- Metal 기반 inference optimization
- unified memory profiling

향후 MLX 등 다른 Apple-native inference runtime도 확장 대상으로 고려합니다.

## Runtime과 Profile

머신별 경로와 ComfyUI 실행 환경은 runtime config에 둡니다.

```toml
name = "local-comfyui"
base_directory = "/path/to/comfyui-model-base"
```

Profile은 비교할 실험 변수만 유지합니다.

```toml
name = "metal-flash"
server_args = ["--use-flash-attention"]
```

API workflow에 등장하는 `class_type`은 자동으로 runtime 요구사항이 됩니다. `comfy-metal preflight`는 이미지를 생성하지 않고 ComfyUI의 `/object_info`와 비교해 필요한 node가 모두 존재하는지 확인합니다.

## Workload manifest

코어는 특정 모델이나 sampler의 seed 위치를 하드코딩하지 않습니다. workload가 cold/warm 사이에 바꿀 API workflow input과 benchmark output node를 명시합니다.

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

일반적인 API-format workflow는 다음 명령으로 mutation/output 후보를 탐지할 수 있습니다. 후보가 하나씩으로 명확하면 `--write`로 manifest도 생성할 수 있습니다.

```bash
comfy-metal inspect workflow_api.json --write workload.toml
```

EasyUse처럼 설정이 JSON 문자열 안에 들어 있는 경우에는 `path = "sampler.seed"`, `format = "json"` 형태의 nested mutation을 사용할 수 있습니다.

같은 workflow에서 고정된 실험 variant를 만들 때는 `[[overrides]]`를 사용합니다. Override는 실행 전에 적용되고 cold/warm 사이에는 바뀌지 않습니다.

```toml
[[overrides]]
node = "12"
input = "enabled"
value = false
```

`overrides`와 `session.mutations`는 역할이 다르며 같은 target을 동시에 지정할 수 없습니다.

## Case study

- [Spectrum KSampler ablation on Apple Silicon](docs/case-studies/spectrum-ksampler-ablation.md)

## CLI

> 아직 초기 개발 단계이며 인터페이스는 변경될 수 있습니다.

```bash
comfy-metal preflight \
  --comfyui-root ~/projects/ComfyUI \
  --workload workloads/example/workload.toml \
  --runtime runtimes/local.toml \
  --profile profiles/stock.toml

comfy-metal bench \
  --comfyui-root ~/projects/ComfyUI \
  --workload workloads/example/workload.toml \
  --runtime runtimes/local.toml \
  --profile profiles/metal-flash.toml \
  --sessions 3
```

## 현재 상태

초기 개발 단계입니다.

첫 번째 milestone은 하나의 신뢰할 수 있는 baseline benchmark를 만드는 것입니다.

- deterministic workload
- isolated cold/warm sessions
- model-cold / model-warm measurements
- wall-clock timing
- MPS memory metrics
- JSON report
- SSIM quality regression check

## 라이선스

Apache License 2.0으로 배포합니다. 자세한 내용은 [`LICENSE`](LICENSE)를 참고하세요.
