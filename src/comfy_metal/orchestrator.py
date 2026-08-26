"""Subprocess orchestration primitives for benchmark isolation.

The subprocess-per-repetition pattern and sentinel-based result transport are
adapted from mlx-teacache's `scripts/bench_speedup.py` (Copyright 2026 Denis
Ineshin), licensed under Apache-2.0. Modified for Comfy Metal Lab.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Sequence

from .protocol import parse_worker_result


@dataclass(frozen=True)
class WorkerExecution:
    """Raw worker process output plus its parsed benchmark result."""

    label: str
    result: dict
    stdout: str
    stderr: str


def run_worker_process(command: Sequence[str], *, label: str) -> WorkerExecution:
    """Run one benchmark worker in a fresh subprocess and parse its result."""

    process = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "no worker output"
        raise RuntimeError(f"worker failed for {label}: exit {process.returncode}: {detail}")

    return WorkerExecution(
        label=label,
        result=parse_worker_result(process.stdout),
        stdout=process.stdout,
        stderr=process.stderr,
    )
