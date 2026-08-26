import time
from pathlib import Path

import pytest

from comfy_metal.telemetry import MacSwapWatcher, parse_swap_used_gib


def test_parse_swap_used_gib_handles_macos_sysctl():
    output = "vm.swapusage: total = 4096.00M  used = 1536.00M  free = 2560.00M  (encrypted)"
    assert parse_swap_used_gib(output) == pytest.approx(1.5)


def test_swap_watcher_writes_samples_and_growth(tmp_path: Path):
    values = iter([1.0, 1.5, 1.25, 1.25])

    def sample():
        return next(values, 1.25)

    path = tmp_path / "swap.jsonl"
    watcher = MacSwapWatcher(path, interval_seconds=0.005, sample_fn=sample)
    watcher.start()
    deadline = time.monotonic() + 1.0
    while len(watcher.samples) < 3 and time.monotonic() < deadline:
        time.sleep(0.005)
    summary = watcher.stop()

    assert summary["samples"] >= 3
    assert summary["start_swap_used_gib"] == 1.0
    assert summary["max_swap_used_gib"] == 1.5
    assert summary["swap_growth_gib"] == 0.5
    assert len(path.read_text().splitlines()) >= 3
