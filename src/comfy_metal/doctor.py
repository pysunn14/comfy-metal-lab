"""Benchmark-readiness diagnostics for Comfy Metal Lab."""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from .comfyui import ComfyUIClient, ComfyUIProcess, ComfyUIServerConfig, MPSMemoryClient
from .config import ProfileConfig, RuntimeConfig, load_profile, load_runtime
from .environment import parse_load_average, parse_memory_free_percent, thermal_state
from .telemetry import parse_swap_used_gib

CheckStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]
    evidence: dict[str, Any] | None = None

    @property
    def readiness(self) -> str:
        statuses = {check.status for check in self.checks}
        if "fail" in statuses:
            return "BLOCKED"
        if "warn" in statuses:
            return "WARN"
        return "READY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "readiness": self.readiness,
            "checks": [check.to_dict() for check in self.checks],
            "evidence": self.evidence or {},
        }


def _run_text(command: list[str], *, timeout_s: float = 5.0) -> str | None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _machine_snapshot() -> dict[str, Any]:
    memory = _run_text(["/usr/bin/memory_pressure", "-Q"]) if platform.system() == "Darwin" else None
    swap = _run_text(["/usr/sbin/sysctl", "vm.swapusage"]) if platform.system() == "Darwin" else None
    load = _run_text(["/usr/sbin/sysctl", "-n", "vm.loadavg"]) if platform.system() == "Darwin" else None
    thermal = _run_text(["/usr/bin/pmset", "-g", "therm"]) if platform.system() == "Darwin" else None
    battery = _run_text(["/usr/bin/pmset", "-g", "batt"]) if platform.system() == "Darwin" else None
    chip = _run_text(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]) if platform.system() == "Darwin" else None
    power_source = None
    if battery:
        marker = "Now drawing from '"
        if marker in battery:
            power_source = battery.split(marker, 1)[1].split("'", 1)[0]
    return {
        "system": platform.system(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "chip": chip,
        "memory_free_percent": parse_memory_free_percent(memory),
        "swap_used_gib": parse_swap_used_gib(swap),
        "load_average": parse_load_average(load),
        "thermal_state": thermal_state(thermal),
        "power_source": power_source,
    }


def _probe_runtime_python(python: Path) -> dict[str, Any]:
    script = (
        "import json, platform, torch; "
        "print(json.dumps({'python': platform.python_version(), "
        "'torch': torch.__version__, "
        "'mps_available': bool(torch.backends.mps.is_available())}))"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=20.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"python": None, "torch": None, "mps_available": None, "error": f"{type(exc).__name__}: {exc}"}
    if completed.returncode != 0:
        return {
            "python": None,
            "torch": None,
            "mps_available": None,
            "error": completed.stderr.strip() or f"runtime probe exited {completed.returncode}",
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"python": None, "torch": None, "mps_available": None, "error": f"invalid probe JSON: {exc}"}
    return {**payload, "error": None}


def _git_dirty(path: Path) -> bool | None:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return bool(completed.stdout.strip())


def _active_processes() -> list[dict[str, Any]]:
    if platform.system() != "Darwin":
        return []
    output = _run_text(["/bin/ps", "-axo", "pid=,command="], timeout_s=5.0)
    if not output:
        return []
    current_pid = os.getpid()
    markers = (
        "ComfyUI/main.py",
        "comfy_metal.worker",
        "comfy-metal bench",
        "comfy_metal.runtime_entry",
        "runtime_entry.py",
    )
    matches: list[dict[str, Any]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        if any(marker in command for marker in markers):
            matches.append({"pid": pid, "command": command})
    return matches


def _free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _startup_probe(
    *,
    comfyui_root: Path,
    runtime: RuntimeConfig,
    profile: ProfileConfig,
    startup_timeout_s: float,
) -> dict[str, Any]:
    host = "127.0.0.1"
    port = _free_port(host)
    telemetry_port = _free_port(host)
    telemetry_token = uuid.uuid4().hex
    config = ComfyUIServerConfig(
        root=comfyui_root,
        python=runtime.python,
        host=host,
        port=port,
        telemetry_host=host,
        telemetry_port=telemetry_port,
        telemetry_token=telemetry_token,
        base_directory=runtime.base_directory,
        extra_model_paths=runtime.extra_model_paths,
        extra_args=(*runtime.server_args, *profile.server_args),
        startup_timeout_s=startup_timeout_s,
    )
    with TemporaryDirectory(prefix="comfy-metal-doctor-") as temp_dir:
        log_path = Path(temp_dir) / "comfyui.log"
        config.validate()
        server = ComfyUIProcess(config)
        env = os.environ.copy()
        env["COMFY_METAL_TELEMETRY_TOKEN"] = telemetry_token
        with log_path.open("wb") as log:
            server.process = subprocess.Popen(
                config.command(),
                cwd=config.root,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                startup_s = server.wait_until_ready()
                stats = ComfyUIClient(host=host, port=port).system_stats()
                mps_snapshot = MPSMemoryClient(
                    host=host, port=telemetry_port, token=telemetry_token
                ).snapshot()
            except Exception as exc:
                server.stop()
                log.flush()
                try:
                    tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
                except OSError:
                    tail = []
                detail = "\n".join(tail)
                if detail:
                    raise RuntimeError(f"{exc}\nComfyUI log tail:\n{detail}") from exc
                raise
            else:
                server.stop()
        return {
            "startup_s": startup_s,
            "system_stats": stats,
            "mps_telemetry": mps_snapshot,
        }


def _workspace_check(root: Path) -> tuple[DoctorCheck, dict[str, Any]]:
    expected = ("workloads", "runtimes", "profiles", "results")
    missing = [name for name in expected if not (root / name).is_dir()]
    if missing:
        return (
            DoctorCheck("Managed workspace", "fail", f"missing {', '.join(missing)} under {root}; run `comfy-metal init`"),
            {"root": str(root), "missing": missing, "writable": False},
        )
    results = root / "results"
    probe = results / ".doctor-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return (
            DoctorCheck("Managed workspace", "fail", f"results directory is not writable: {exc}"),
            {"root": str(root), "missing": [], "writable": False},
        )
    return DoctorCheck("Managed workspace", "pass", f"initialized and writable at {root}"), {"root": str(root), "missing": [], "writable": True}


def _runtime_path_checks(comfyui_root: Path, runtime: RuntimeConfig) -> tuple[list[DoctorCheck], Path]:
    checks: list[DoctorCheck] = []
    main = comfyui_root / "main.py"
    python = runtime.python or comfyui_root / ".venv" / "bin" / "python"
    if not comfyui_root.is_dir() or not main.is_file():
        checks.append(DoctorCheck("ComfyUI checkout", "fail", f"main.py not found under {comfyui_root}"))
    else:
        checks.append(DoctorCheck("ComfyUI checkout", "pass", f"found {main}"))
    if not python.is_file():
        checks.append(DoctorCheck("ComfyUI Python", "fail", f"Python executable not found: {python}"))
    else:
        checks.append(DoctorCheck("ComfyUI Python", "pass", str(python)))
    if runtime.base_directory is not None:
        if runtime.base_directory.is_dir():
            checks.append(DoctorCheck("Runtime base directory", "pass", str(runtime.base_directory)))
        else:
            checks.append(DoctorCheck("Runtime base directory", "fail", f"directory does not exist: {runtime.base_directory}"))
    missing_extra = [str(path) for path in runtime.extra_model_paths if not path.is_file()]
    if missing_extra:
        checks.append(DoctorCheck("Extra model paths", "fail", f"missing config file(s): {', '.join(missing_extra)}"))
    elif runtime.extra_model_paths:
        checks.append(DoctorCheck("Extra model paths", "pass", f"{len(runtime.extra_model_paths)} config file(s) found"))
    return checks, python


def run_doctor(
    *,
    comfyui_root: Path,
    runtime_path: Path,
    profile_path: Path,
    workspace_root: Path = Path(".comfy-metal"),
    startup_timeout_s: float = 60.0,
) -> DoctorReport:
    """Check whether the selected runtime/profile is suitable for benchmark execution."""

    checks: list[DoctorCheck] = []
    evidence: dict[str, Any] = {}

    workspace_check, workspace_evidence = _workspace_check(workspace_root)
    checks.append(workspace_check)
    evidence["workspace"] = workspace_evidence

    try:
        runtime = load_runtime(runtime_path)
        checks.append(DoctorCheck("Runtime config", "pass", f"{runtime.name}: {runtime_path}"))
    except Exception as exc:
        checks.append(DoctorCheck("Runtime config", "fail", str(exc)))
        return DoctorReport(tuple(checks), evidence)
    try:
        profile = load_profile(profile_path)
        checks.append(DoctorCheck("Profile config", "pass", f"{profile.name}: {profile_path}"))
    except Exception as exc:
        checks.append(DoctorCheck("Profile config", "fail", str(exc)))
        return DoctorReport(tuple(checks), evidence)

    path_checks, python = _runtime_path_checks(comfyui_root, runtime)
    checks.extend(path_checks)

    machine = _machine_snapshot()
    evidence["machine"] = machine
    if machine.get("system") != "Darwin" or machine.get("machine") != "arm64":
        checks.append(DoctorCheck("Apple Silicon", "fail", f"expected macOS arm64, got {machine.get('platform')} / {machine.get('machine')}"))
    else:
        checks.append(DoctorCheck("Apple Silicon", "pass", machine.get("chip") or "macOS arm64"))

    if python.is_file():
        runtime_probe = _probe_runtime_python(python)
    else:
        runtime_probe = {"python": None, "torch": None, "mps_available": None, "error": "Python executable missing"}
    evidence["runtime_python"] = runtime_probe
    if runtime_probe.get("error"):
        checks.append(DoctorCheck("PyTorch runtime", "fail", str(runtime_probe["error"])))
    else:
        checks.append(DoctorCheck("PyTorch runtime", "pass", f"Python {runtime_probe.get('python')}, PyTorch {runtime_probe.get('torch')}"))
        if runtime_probe.get("mps_available") is True:
            checks.append(DoctorCheck("MPS", "pass", "torch.backends.mps.is_available() = true"))
        else:
            checks.append(DoctorCheck("MPS", "fail", "torch.backends.mps.is_available() = false"))

    dirty = _git_dirty(comfyui_root) if comfyui_root.is_dir() else None
    evidence["comfyui_git_dirty"] = dirty
    if dirty is True:
        checks.append(DoctorCheck("ComfyUI Git", "warn", "working tree is dirty; provenance will record this"))
    elif dirty is False:
        checks.append(DoctorCheck("ComfyUI Git", "pass", "working tree is clean"))

    active = _active_processes()
    evidence["active_processes"] = active
    if active:
        checks.append(DoctorCheck("Existing benchmark processes", "warn", f"found {len(active)} potentially competing ComfyUI/benchmark process(es)"))
    else:
        checks.append(DoctorCheck("Existing benchmark processes", "pass", "none detected"))

    thermal = machine.get("thermal_state")
    if thermal == "reported":
        checks.append(DoctorCheck("Thermal state", "warn", "macOS reports a thermal/performance condition"))
    elif thermal == "no-recorded-warning":
        checks.append(DoctorCheck("Thermal state", "pass", "no recorded thermal/performance warning"))
    else:
        checks.append(DoctorCheck("Thermal state", "warn", "thermal state unavailable"))

    power = machine.get("power_source")
    if power and power != "AC Power":
        checks.append(DoctorCheck("Power source", "warn", f"running on {power}"))
    elif power == "AC Power":
        checks.append(DoctorCheck("Power source", "pass", "AC Power"))

    # Memory and load are evidence. Pre-existing swap is a warning, not a failure: macOS
    # may retain swapped pages after pressure has subsided, but small timing claims deserve caution.
    swap_used = machine.get("swap_used_gib")
    machine_status: CheckStatus = "warn" if isinstance(swap_used, (int, float)) and swap_used > 0 else "pass"
    memory_free = machine.get("memory_free_percent")
    memory_text = f"{float(memory_free):.1f}%" if isinstance(memory_free, (int, float)) else "unavailable"
    swap_text = f"{float(swap_used):.2f} GiB" if isinstance(swap_used, (int, float)) else "unavailable"
    load_values = machine.get("load_average")
    load_text = (
        "/".join(f"{float(value):.2f}" for value in load_values)
        if isinstance(load_values, list)
        else "unavailable"
    )
    machine_detail = f"memory free={memory_text}, swap={swap_text}, load={load_text}"
    if machine_status == "warn":
        machine_detail += "; pre-existing swap detected (not a failure, but review small timing differences carefully)"
    checks.append(DoctorCheck("Machine state", machine_status, machine_detail))

    if any(check.status == "fail" for check in checks):
        checks.append(DoctorCheck("ComfyUI startup", "fail", "skipped because a blocking prerequisite failed"))
        return DoctorReport(tuple(checks), evidence)

    try:
        startup = _startup_probe(
            comfyui_root=comfyui_root,
            runtime=runtime,
            profile=profile,
            startup_timeout_s=startup_timeout_s,
        )
    except Exception as exc:
        checks.append(DoctorCheck("ComfyUI startup", "fail", str(exc)))
    else:
        evidence["startup"] = startup
        checks.append(DoctorCheck("ComfyUI startup", "pass", f"/system_stats ready in {float(startup['startup_s']):.2f}s"))
        telemetry = startup.get("mps_telemetry")
        if isinstance(telemetry, dict) and telemetry.get("available") is True:
            checks.append(DoctorCheck("MPS telemetry", "pass", "runtime_entry allocator telemetry responded"))
        else:
            checks.append(DoctorCheck("MPS telemetry", "fail", f"allocator telemetry unavailable: {telemetry}"))

    return DoctorReport(tuple(checks), evidence)


def format_doctor_report(report: DoctorReport) -> str:
    symbols = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    lines = ["Comfy Metal Doctor", ""]
    for check in report.checks:
        lines.append(f"[{symbols[check.status]}] {check.name}: {check.detail}")
    lines.extend(["", f"Benchmark readiness: {report.readiness}"])
    if report.readiness == "WARN":
        lines.append("Timing may be usable, but review warnings before small performance comparisons.")
    elif report.readiness == "BLOCKED":
        lines.append("Fix blocking checks before running a benchmark.")
    return "\n".join(lines)
