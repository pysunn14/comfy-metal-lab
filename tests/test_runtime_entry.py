from __future__ import annotations

from pathlib import Path

from comfy_metal.runtime_entry import MemoryProbe


class _FakeMemory:
    def __init__(self) -> None:
        self.reset_calls = 0

    def reset_peak_memory_stats(self) -> None:
        self.reset_calls += 1

    @staticmethod
    def memory_allocated() -> int:
        return 100

    @staticmethod
    def max_memory_allocated() -> int:
        return 300

    @staticmethod
    def memory_reserved() -> int:
        return 400

    @staticmethod
    def max_memory_reserved() -> int:
        return 500


class _FakeAccelerator:
    def __init__(self, memory: _FakeMemory) -> None:
        self.memory = memory

    @staticmethod
    def current_accelerator() -> str:
        return "mps"


class _FakeMPS:
    def __init__(self) -> None:
        self.sync_calls = 0

    def synchronize(self) -> None:
        self.sync_calls += 1

    @staticmethod
    def current_allocated_memory() -> int:
        return 110

    @staticmethod
    def driver_allocated_memory() -> int:
        return 600

    @staticmethod
    def recommended_max_memory() -> int:
        return 700


class _FakeTorch:
    def __init__(self) -> None:
        self._memory = _FakeMemory()
        self.accelerator = _FakeAccelerator(self._memory)
        self.mps = _FakeMPS()



def test_memory_probe_resets_and_reports_mps_allocator_stats() -> None:
    torch = _FakeTorch()
    probe = MemoryProbe(torch_module=torch)

    reset = probe.reset_peak()
    snapshot = probe.snapshot()

    assert reset == {"available": True, "backend": "mps", "reset": True}
    assert torch._memory.reset_calls == 1
    assert torch.mps.sync_calls == 2
    assert snapshot == {
        "available": True,
        "backend": "mps",
        "allocated_end_bytes": 110,
        "reserved_end_bytes": 400,
        "peak_allocated_bytes": 300,
        "peak_reserved_bytes": 500,
        "driver_allocated_end_bytes": 600,
        "recommended_max_bytes": 700,
    }


def test_runtime_wrapper_flushes_atexit_trace_on_sigterm(tmp_path: Path) -> None:
    import os
    import socket
    import subprocess
    import sys
    import time

    target = tmp_path / "fake_main.py"
    ready = tmp_path / "ready"
    flushed = tmp_path / "flushed"
    target.write_text(
        "import atexit, os, time\n"
        f"ready = {str(ready)!r}\n"
        f"flushed = {str(flushed)!r}\n"
        "atexit.register(lambda: open(flushed, 'w').write(os.environ.get('MTLFLASHATTN_TRACE', 'missing')))\n"
        "open(ready, 'w').write('ready')\n"
        "while True: time.sleep(0.1)\n"
    )

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    env = os.environ.copy()
    env["COMFY_METAL_TELEMETRY_TOKEN"] = "test-token"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "comfy_metal.runtime_entry",
            "--telemetry-port",
            str(port),
            "--",
            str(target),
            "--use-flash-attention",
        ],
        env=env,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        assert ready.exists(), "runtime wrapper target did not start"

        process.terminate()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert flushed.read_text() == "1"
