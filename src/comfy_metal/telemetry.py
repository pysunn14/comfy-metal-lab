"""Best-effort non-privileged macOS telemetry used during benchmark sessions."""

from __future__ import annotations

import json
import platform
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_SWAP_USED_RE = re.compile(r"used\s*=\s*([0-9.]+)([KMG])", re.IGNORECASE)


def parse_swap_used_gib(output: str | None) -> float | None:
    if not output:
        return None
    match = _SWAP_USED_RE.search(output)
    if match is None:
        return None
    value = float(match.group(1))
    multiplier = {"K": 1 / 1024**2, "M": 1 / 1024, "G": 1}[match.group(2).upper()]
    return value * multiplier


def sample_swap_used_gib() -> float | None:
    if platform.system() != "Darwin":
        return None
    try:
        completed = subprocess.run(
            ["/usr/sbin/sysctl", "vm.swapusage"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return parse_swap_used_gib(completed.stdout)


class MacSwapWatcher:
    """Sample macOS swap usage in a small background thread for one benchmark rep."""

    def __init__(
        self,
        output_path: Path,
        *,
        interval_seconds: float = 1.0,
        sample_fn: Callable[[], float | None] = sample_swap_used_gib,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("swap telemetry interval must be positive")
        self.output_path = output_path
        self.interval_seconds = interval_seconds
        self.sample_fn = sample_fn
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _record(self) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "monotonic_seconds": time.monotonic(),
            "swap_used_gib": self.sample_fn(),
        }
        self.samples.append(event)
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    def _run(self) -> None:
        while not self._stop.is_set():
            self._record()
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("swap watcher already started")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text("", encoding="utf-8")
        self._thread = threading.Thread(target=self._run, daemon=True, name="comfy-metal-swap")
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.interval_seconds * 2))
            self._thread = None
        values = [
            float(sample["swap_used_gib"])
            for sample in self.samples
            if sample["swap_used_gib"] is not None
        ]
        start = values[0] if values else None
        maximum = max(values) if values else None
        return {
            "samples": len(self.samples),
            "start_swap_used_gib": start,
            "max_swap_used_gib": maximum,
            "swap_growth_gib": (maximum - start) if start is not None and maximum is not None else None,
            "path": str(self.output_path),
        }

    def __enter__(self) -> MacSwapWatcher:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.stop()
