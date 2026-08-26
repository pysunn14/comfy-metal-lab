"""Sentinel-based result protocol for isolated benchmark subprocesses.

Portions of the transport design are adapted from mlx-teacache's
`scripts/bench_speedup.py` (Copyright 2026 Denis Ineshin), licensed under
Apache-2.0. Modified for Comfy Metal Lab.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

RESULT_SENTINEL = "::BENCH_RESULT::"


@dataclass(frozen=True)
class WorkerResult:
    """Machine-readable result for the single-generation `run` command."""

    condition: str
    rep: int
    elapsed_s: float
    peak_memory_mb: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationResult:
    """One cold or warm prompt executed inside a benchmark session."""

    phase: str
    elapsed_s: float
    peak_memory_mb: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SessionWorkerResult:
    """Cold and warm generations produced by one fresh worker/ComfyUI session."""

    condition: str
    session: int
    server_startup_s: float
    runtime_preflight: dict[str, Any]
    cold: GenerationResult
    warm: GenerationResult

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def emit_worker_result(result: WorkerResult | SessionWorkerResult) -> str:
    """Serialize a worker result as a sentinel-prefixed stdout line."""

    return f"{RESULT_SENTINEL}{json.dumps(result.to_dict(), sort_keys=True)}"


def parse_worker_result(stdout: str) -> dict[str, Any]:
    """Extract the last sentinel-prefixed benchmark result from worker stdout."""

    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_SENTINEL):
            payload = line[len(RESULT_SENTINEL) :]
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                raise ValueError("worker result payload must be a JSON object")
            return parsed
    raise ValueError(f"worker did not emit a {RESULT_SENTINEL} result line")
