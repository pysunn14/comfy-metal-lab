# Comfy Metal Lab

> Apple Silicon에서 ComfyUI 추론을 재현 가능하게 벤치마크하고 최적화하기 위한 오픈소스 실험 하네스

[English README](README.md)

Comfy Metal Lab은 Apple Silicon 환경에서 ComfyUI 이미지 생성 워크로드의 성능을 측정하고, MPS/Metal 기반 최적화를 동일한 조건에서 비교하기 위한 프로젝트입니다.

ComfyUI 자체를 포크하지 않고 **외부 runtime**으로 사용하며, 벤치마크 설정과 workload를 별도로 관리합니다.

## 핵심 개념

Comfy Metal Lab은 실행 환경을 세 가지로 나눕니다.

- **Runtime** — ComfyUI checkout, Python/PyTorch/MPS, 모델 경로, 설치된 custom node 등 머신별 실행 환경
- **Workload** — API workflow, 필요한 node, cold/warm mutation, output 등 실행 대상
- **Profile** — attention backend나 precision처럼 비교하려는 실험 변수

> **Workload는 무엇을 실행하는지, Runtime은 그것을 실행할 수 있는 환경인지, Profile은 어떤 실험 변수를 적용할지 정의합니다.**

성능 비교에서는 workload와 runtime을 고정하고 profile만 바꿉니다.

## 에이전트로 시작하기

코딩 에이전트에게 맡길 때는 먼저 [`AGENTS.md`](AGENTS.md)를 읽게 하면 됩니다. 실제 사용할 workflow/workload를 사용자가 지정하는 것을 기본으로 하며, 대상이 불명확하면 에이전트가 로컬 파일에서 임의로 고르지 않고 먼저 물어보도록 합니다. `doctor`와 `preflight`는 준비 상태 확인에 사용할 수 있지만, 실제 이미지 생성 benchmark는 사용자가 측정을 명확히 요청했을 때만 실행합니다.

> `AGENTS.md`를 먼저 읽고 managed workspace와 runtime을 확인해줘. 내가 정확한 workflow/workload를 지정하지 않았다면 어떤 대상을 사용할지 먼저 물어봐. 대상이 정해지면 doctor와 preflight까지 준비하고, 내가 실제 측정을 요청한 경우에만 benchmark를 실행해줘.

## 설치

`uv`로 CLI를 한 번 설치하면 이후에는 셸에서 `comfy-metal`을 바로 사용할 수 있습니다.

```bash
uv tool install git+https://github.com/pysunn14/comfy-metal-lab.git
```

## 빠른 시작

프로젝트별 로컬 작업공간을 만들고, 디스크 어디에 있는 API-format ComfyUI workflow든 가져올 수 있습니다.

```bash
comfy-metal init
comfy-metal doctor
comfy-metal import-workload ~/Downloads/workflow_api.json --name my-workload
```

`.comfy-metal/` 아래에 workload, runtime, profile, result가 정리됩니다. 기본 `local` runtime과 `stock` profile도 자동 생성됩니다. 인접한 위치에서 명확한 `ComfyUI` checkout을 찾으면 `init`이 기본 runtime의 `comfyui_root`에 기록하므로 이후 명령에서 경로를 반복해서 넣지 않아도 됩니다.

실제 benchmark 전에 doctor로 머신과 runtime이 측정 가능한 상태인지 확인합니다. doctor는 이미지를 생성하지 않고 Python/PyTorch/MPS, runtime 경로, machine state, 경쟁 process, 실제 ComfyUI startup과 MPS telemetry를 확인해 `READY`, `WARN`, `BLOCKED` 중 하나를 반환합니다.

```bash
comfy-metal doctor
```

필요한 경우에만 `--comfyui-root`를 명시해 runtime 설정을 일시적으로 override할 수 있습니다.

그다음 workload에 필요한 custom node가 실제 runtime에 있는지 preflight하고 benchmark를 실행합니다.

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

`--output-dir`를 생략하면 `.comfy-metal/results/` 아래에 새 결과 디렉터리가 자동으로 할당됩니다. 명시적인 파일 경로 방식도 그대로 지원합니다.

## 목표

- Apple Silicon에서 재현 가능한 ComfyUI benchmark
- cold / warm inference 측정
- wall-clock latency 및 MPS memory 기록
- optimization별 성능 비교
- machine-readable JSON report
- 이미지 품질 regression check
- 프로젝트별 managed workspace 제공

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
comfyui_root = "/path/to/ComfyUI"
base_directory = "/path/to/comfyui-model-base"
```

Profile은 비교할 실험 변수만 유지합니다.

```toml
name = "metal-flash"
server_args = ["--use-flash-attention"]
```

API workflow에 등장하는 `class_type`은 자동으로 runtime 요구사항이 됩니다. `comfy-metal preflight`는 이미지를 생성하지 않고 ComfyUI의 `/object_info`와 비교해 필요한 node가 모두 존재하는지 확인합니다. `--comfyui-root`는 필요할 때 runtime의 경로를 일시적으로 override하는 용도로 계속 사용할 수 있습니다.

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

일반적인 API-format workflow는 `comfy-metal import-workload workflow_api.json`으로 managed workspace에 가져올 수 있습니다. seed/noise와 `SaveImage` 후보가 명확하면 workload manifest도 자동 생성됩니다. `comfy-metal inspect`는 읽기 전용 또는 수동 설정용으로 그대로 사용할 수 있습니다.

EasyUse처럼 설정이 JSON 문자열 안에 들어 있는 경우에는 `path = "sampler.seed"`, `format = "json"` 형태의 nested mutation을 사용할 수 있습니다.

같은 workflow에서 고정된 실험 variant를 만들 때는 `[[overrides]]`를 사용합니다. Override는 실행 전에 적용되고 cold/warm 사이에는 바뀌지 않습니다.

```toml
[[overrides]]
node = "12"
input = "enabled"
value = false
```

`overrides`와 `session.mutations`는 역할이 다르며 같은 target을 동시에 지정할 수 없습니다.

## 비교

`comfy-metal compare`는 기존의 엄격한 profile comparison입니다. workload와 runtime이 같아야 하며, SSIM은 correctness gate로 동작합니다.

더 넓은 실험은 `comfy-metal compare-contract`에서 명시적인 `vary` contract를 사용합니다. `vary`에 선언한 factor만 달라도 되고, 선언하지 않은 factor는 계속 같아야 합니다.

```toml
name = "base-vs-turbo"
vary = ["workload"]
```

`workload`가 바뀌는 비교에서는 SSIM을 pass/fail gate가 아니라 설명용 유사도 지표로 기록합니다. `vary = ["workload", "profile"]`처럼 여러 factor를 함께 바꾸면 결과는 개별 요인이 아니라 결합된 stack 효과로 해석합니다.

## CLI 요약

일반적인 사용 순서는 다음과 같습니다.

```text
init → import-workload → doctor → preflight → bench → compare / compare-contract
```

`inspect`는 workflow를 읽기 전용으로 분석하거나 manifest target을 수동으로 확인할 때 사용할 수 있습니다.

## 현재 상태

현재 하네스는 실제 ComfyUI workload를 가져와 runtime readiness를 확인하고, 호환성 preflight 후 재현 가능한 cold/warm benchmark와 품질 비교까지 수행할 수 있습니다.

- managed `.comfy-metal/` workspace
- `doctor` readiness check
- workflow import / inspection / static override
- isolated cold/warm sessions
- wall-clock + MPS memory + swap/environment telemetry
- machine-readable JSON report
- SSIM quality regression check

작은 성능 차이에 대한 interleaved/randomized 실행 같은 고급 실험 기능은 필요할 때 추가하는 방향입니다.

## 라이선스

Apache License 2.0으로 배포합니다. 자세한 내용은 [`LICENSE`](LICENSE)를 참고하세요.
