"""Reproducibility metadata and cheap macOS benchmark preflight snapshots."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

_HARNESS_ROOT = Path(__file__).resolve().parents[2]
_MEMORY_FREE_RE = re.compile(r"System-wide memory free percentage:\s*([0-9.]+)%")
_LOAD_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_POWER_RE = re.compile(r"Now drawing from '([^']+)'", re.IGNORECASE)


def _capture(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_s: float = 5.0,
) -> tuple[int | None, str, str]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, "", f"{type(exc).__name__}: {exc}"
    return completed.returncode, completed.stdout, completed.stderr


def _text(command: Sequence[str], *, cwd: Path | None = None) -> str | None:
    returncode, stdout, _stderr = _capture(command, cwd=cwd)
    if returncode != 0:
        return None
    value = stdout.strip()
    return value or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def parse_memory_free_percent(output: str | None) -> float | None:
    if not output:
        return None
    match = _MEMORY_FREE_RE.search(output)
    return float(match.group(1)) if match else None


def parse_load_average(output: str | None) -> list[float] | None:
    if not output:
        return None
    values = [float(value) for value in _LOAD_RE.findall(output)]
    return values[:3] if len(values) >= 3 else None


def thermal_state(output: str | None) -> str:
    if not output:
        return "unavailable"
    lowered = output.lower()
    if (
        "no thermal warning level has been recorded" in lowered
        and "no performance warning level has been recorded" in lowered
    ):
        return "no-recorded-warning"
    return "reported"


def _git_snapshot(path: Path) -> dict[str, Any]:
    commit = _text(["git", "rev-parse", "HEAD"], cwd=path)
    status = _text(["git", "status", "--porcelain"], cwd=path)
    return {
        "commit": commit,
        "dirty": None if commit is None else bool(status),
    }


def _hardware_snapshot() -> dict[str, Any]:
    chip = _text(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"])
    ram_text = _text(["/usr/sbin/sysctl", "-n", "hw.memsize"])
    ram_gib: float | None = None
    if ram_text is not None:
        try:
            ram_gib = round(int(ram_text) / (1024**3), 2)
        except ValueError:
            ram_gib = None
    return {
        "chip": chip or platform.processor() or None,
        "ram_gib": ram_gib,
        "machine": platform.machine(),
    }


def _runtime_snapshot(comfyui_root: Path) -> dict[str, Any]:
    python = comfyui_root / ".venv" / "bin" / "python"
    if not python.is_file():
        return {
            "python_executable": str(python),
            "python": None,
            "torch": None,
            "mps_available": None,
            "error": "ComfyUI Python executable not found",
        }

    script = (
        "import json, platform, torch; "
        "print(json.dumps({'python': platform.python_version(), "
        "'torch': torch.__version__, "
        "'mps_available': bool(torch.backends.mps.is_available())}))"
    )
    returncode, stdout, stderr = _capture([str(python), "-c", script], timeout_s=20.0)
    if returncode != 0:
        return {
            "python_executable": str(python),
            "python": None,
            "torch": None,
            "mps_available": None,
            "error": stderr.strip() or f"runtime probe exited {returncode}",
        }
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {
            "python_executable": str(python),
            "python": None,
            "torch": None,
            "mps_available": None,
            "error": f"invalid runtime probe JSON: {exc}",
        }
    return {"python_executable": str(python), **payload, "error": None}


def _system_snapshot() -> dict[str, Any]:
    memory_raw = _text(["/usr/bin/memory_pressure", "-Q"])
    load_raw = _text(["/usr/sbin/sysctl", "-n", "vm.loadavg"])
    thermal_raw = _text(["/usr/bin/pmset", "-g", "therm"])
    battery_raw = _text(["/usr/bin/pmset", "-g", "batt"])
    power_match = _POWER_RE.search(battery_raw or "")
    return {
        "load_average": parse_load_average(load_raw),
        "memory_free_percent": parse_memory_free_percent(memory_raw),
        "thermal": {
            "state": thermal_state(thermal_raw),
            "raw": thermal_raw,
        },
        "power_source": power_match.group(1) if power_match else None,
    }


def collect_environment_snapshot(
    *,
    comfyui_root: Path,
    workload_path: Path,
    workflow_path: Path,
    runtime_path: Path,
    profile_path: Path,
) -> dict[str, Any]:
    """Capture non-privileged benchmark provenance and macOS system state."""

    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "hardware": _hardware_snapshot(),
        "software": {
            "host_python": platform.python_version(),
            "platform": platform.platform(),
            "macos": platform.mac_ver()[0] or None,
        },
        "runtime": _runtime_snapshot(comfyui_root),
        "comfyui_git": _git_snapshot(comfyui_root),
        "harness_git": _git_snapshot(_HARNESS_ROOT),
        "provenance": {
            "workload_sha256": _sha256(workload_path),
            "workflow_sha256": _sha256(workflow_path),
            "runtime_sha256": _sha256(runtime_path),
            "profile_sha256": _sha256(profile_path),
        },
        "system": _system_snapshot(),
    }
