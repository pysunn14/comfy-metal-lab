"""Run ComfyUI with a same-process MPS memory telemetry endpoint.

This wrapper is launched with ComfyUI's own Python interpreter. It starts a
loopback-only control endpoint in the same process, then executes the external
ComfyUI ``main.py`` via ``runpy``. The benchmark worker uses the endpoint to
reset allocator peaks immediately before prompt submission and to snapshot MPS
memory after the prompt completes.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import runpy
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class MemoryProbe:
    """Small adapter over PyTorch's accelerator/MPS memory APIs."""

    def __init__(self, *, torch_module: Any | None = None) -> None:
        self._torch = torch_module
        self._import_error: str | None = None
        self._import_attempted = torch_module is not None

    def _load_torch(self) -> Any | None:
        if self._torch is not None or self._import_attempted:
            return self._torch
        self._import_attempted = True
        try:
            import torch  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on runtime env
            self._import_error = f"{type(exc).__name__}: {exc}"
        else:
            self._torch = torch
        return self._torch

    def _unavailable(self, message: str) -> dict[str, Any]:
        return {"available": False, "backend": "mps", "error": message}

    def _ensure_mps(self) -> tuple[Any | None, dict[str, Any] | None]:
        torch = self._load_torch()
        if torch is None:
            return None, self._unavailable(self._import_error or "PyTorch is unavailable")
        try:
            backend = str(torch.accelerator.current_accelerator())
        except Exception as exc:
            return None, self._unavailable(
                f"accelerator query failed: {type(exc).__name__}: {exc}"
            )
        if backend != "mps":
            return None, self._unavailable(f"current accelerator is {backend!r}, not 'mps'")
        return torch, None

    def reset_peak(self) -> dict[str, Any]:
        torch, unavailable = self._ensure_mps()
        if unavailable is not None:
            return unavailable
        assert torch is not None
        try:
            torch.mps.synchronize()
            torch.accelerator.memory.reset_peak_memory_stats()
        except Exception as exc:
            return self._unavailable(f"peak reset failed: {type(exc).__name__}: {exc}")
        return {"available": True, "backend": "mps", "reset": True}

    def snapshot(self) -> dict[str, Any]:
        torch, unavailable = self._ensure_mps()
        if unavailable is not None:
            return unavailable
        assert torch is not None
        try:
            torch.mps.synchronize()
            memory = torch.accelerator.memory
            return {
                "available": True,
                "backend": "mps",
                "allocated_end_bytes": int(torch.mps.current_allocated_memory()),
                "reserved_end_bytes": int(memory.memory_reserved()),
                "peak_allocated_bytes": int(memory.max_memory_allocated()),
                "peak_reserved_bytes": int(memory.max_memory_reserved()),
                "driver_allocated_end_bytes": int(torch.mps.driver_allocated_memory()),
                "recommended_max_bytes": int(torch.mps.recommended_max_memory()),
            }
        except Exception as exc:
            return self._unavailable(f"snapshot failed: {type(exc).__name__}: {exc}")


class _TelemetryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def _handler(probe: MemoryProbe, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def _authorized(self) -> bool:
            supplied = self.headers.get("X-Comfy-Metal-Token", "")
            return hmac.compare_digest(supplied, token)

        def _json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                self._json({"error": "forbidden"}, 403)
                return
            if self.path == "/snapshot":
                self._json(probe.snapshot())
                return
            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._json({"error": "forbidden"}, 403)
                return
            if self.path == "/reset":
                self._json(probe.reset_peak())
                return
            self._json({"error": "not found"}, 404)

    return Handler


def _parse_wrapper_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    if "--" not in argv:
        raise ValueError("runtime wrapper requires '--' before ComfyUI main.py")
    split = argv.index("--")
    wrapper_argv = argv[:split]
    target_argv = argv[split + 1 :]
    if not target_argv:
        raise ValueError("runtime wrapper requires a ComfyUI main.py path")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telemetry-host", default="127.0.0.1")
    parser.add_argument("--telemetry-port", type=int, required=True)
    args = parser.parse_args(wrapper_argv)
    return args, target_argv



def _enable_metal_attention_trace(target_argv: list[str]) -> None:
    """Always trace Metal FlashAttention when the ComfyUI flash backend is requested."""

    if "--use-flash-attention" in target_argv:
        os.environ["MTLFLASHATTN_TRACE"] = "1"


def _install_graceful_sigterm() -> None:
    """Convert SIGTERM into normal Python shutdown so atexit evidence is flushed."""

    def _handle_sigterm(_signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signal.SIGTERM)

    signal.signal(signal.SIGTERM, _handle_sigterm)

def main(argv: list[str] | None = None) -> int:
    args, target_argv = _parse_wrapper_args(list(sys.argv[1:] if argv is None else argv))
    main_path = Path(target_argv[0]).resolve()
    _enable_metal_attention_trace(target_argv)
    _install_graceful_sigterm()
    if not main_path.is_file():
        raise FileNotFoundError(f"ComfyUI main.py does not exist: {main_path}")

    token = os.environ.get("COMFY_METAL_TELEMETRY_TOKEN")
    if not token:
        raise RuntimeError("COMFY_METAL_TELEMETRY_TOKEN is required")
    probe = MemoryProbe()
    server = _TelemetryHTTPServer(
        (args.telemetry_host, args.telemetry_port),
        _handler(probe, token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="comfy-metal-mps")
    thread.start()

    old_argv = sys.argv
    sys.argv = [str(main_path), *target_argv[1:]]
    sys.path.insert(0, str(main_path.parent))
    try:
        runpy.run_path(str(main_path), run_name="__main__")
    finally:
        sys.argv = old_argv
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
