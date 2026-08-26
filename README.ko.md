# Comfy Metal Lab

> Apple Silicon에서 ComfyUI 추론을 재현 가능하게 벤치마크하고 최적화하기 위한 오픈소스 실험 하네스

Comfy Metal Lab은 Apple Silicon 환경에서 ComfyUI 이미지 생성 워크로드의 성능을 측정하고, MPS/Metal 기반 최적화를 동일한 조건에서 비교하기 위한 프로젝트입니다.

ComfyUI 자체를 포크하지 않고 **외부 runtime**으로 사용하며, 벤치마크 설정과 workload를 별도로 관리합니다.

## 핵심 개념

Comfy Metal Lab은 실행 환경을 세 가지로 나눕니다.

- **Runtime** — ComfyUI, PyTorch, MPS 환경
- **Profile** — precision, attention backend, startup option 등 실행 설정
- **Workload** — workflow, model requirement, resolution, sampler, seed 등 생성 조건

> **Workload는 무엇을 실행하는지, Profile은 그것을 어떻게 실행하는지 정의합니다.**

동일한 workload에 서로 다른 profile을 적용해 최적화 효과를 비교합니다.

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
Workload
   +
Profile
   |
   v
Comfy Metal Lab
   |
   v
External ComfyUI Runtime
```

ComfyUI는 이 저장소에 vendoring하거나 fork하지 않습니다.

## 벤치마크 원칙

단순히 가장 빠른 실행 시간을 기록하는 것이 아니라, **재현 가능하고 설명 가능한 성능 차이**를 측정하는 것을 목표로 합니다.

초기 benchmark harness는 다음 원칙을 따릅니다.

- 반복 실행 격리
- cold / warm 구분
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

## CLI

> 아직 초기 개발 단계이며 인터페이스는 변경될 수 있습니다.

```bash
comfy-metal bench \
  --runtime ~/projects/ComfyUI \
  --workload workloads/anima-base \
  --profile profiles/metal-flash.toml \
  --reps 5
```

## 현재 상태

초기 개발 단계입니다.

첫 번째 milestone은 하나의 신뢰할 수 있는 baseline benchmark를 만드는 것입니다.

- deterministic workload
- isolated repetitions
- cold / warm measurements
- wall-clock timing
- MPS memory metrics
- JSON report
- SSIM quality regression check

## 문서

상세 내용은 `docs/`에서 관리할 예정입니다.

- benchmark methodology
- architecture
- workload specification
- profile specification
- quality evaluation

## 라이선스

Apache License 2.0으로 배포합니다. 자세한 내용은 [`LICENSE`](LICENSE)를 참고하세요.
