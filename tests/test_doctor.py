from __future__ import annotations

from pathlib import Path

from comfy_metal.doctor import DoctorCheck, DoctorReport, run_doctor


def test_doctor_report_readiness_uses_fail_then_warn_then_ready() -> None:
    assert DoctorReport((DoctorCheck("a", "pass", "ok"),)).readiness == "READY"
    assert DoctorReport((DoctorCheck("a", "warn", "hmm"),)).readiness == "WARN"
    assert DoctorReport((DoctorCheck("a", "fail", "bad"), DoctorCheck("b", "warn", "hmm"))).readiness == "BLOCKED"


def test_doctor_runs_real_checks_through_injected_probes(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / ".comfy-metal"
    for name in ("workloads", "runtimes", "profiles", "comparisons", "results", "logs", "docs"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    runtime = workspace / "runtimes" / "local.toml"
    runtime.write_text('name = "local"\nbase_directory = "' + str(tmp_path / "models") + '"\n')
    (tmp_path / "models").mkdir()
    profile = workspace / "profiles" / "stock.toml"
    profile.write_text('name = "stock"\nserver_args = []\n')
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    (comfyui / "main.py").write_text("")
    python = comfyui / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")

    monkeypatch.setattr(
        "comfy_metal.doctor._probe_runtime_python",
        lambda path: {"python": "3.13", "torch": "2.13", "mps_available": True, "error": None},
    )
    monkeypatch.setattr(
        "comfy_metal.doctor._machine_snapshot",
        lambda: {
            "system": "Darwin",
            "platform": "macOS 26.5",
            "machine": "arm64",
            "chip": "Apple Test",
            "memory_free_percent": 55.0,
            "swap_used_gib": 0.0,
            "load_average": [2.0, 1.0, 1.0],
            "thermal_state": "no-recorded-warning",
            "power_source": "AC Power",
        },
    )
    monkeypatch.setattr("comfy_metal.doctor._active_processes", lambda: [])
    monkeypatch.setattr("comfy_metal.doctor._git_dirty", lambda path: False)
    monkeypatch.setattr("comfy_metal.doctor._startup_probe", lambda **kwargs: {"startup_s": 1.25, "system_stats": {"ok": True}, "mps_telemetry": {"available": True}})

    report = run_doctor(
        comfyui_root=comfyui,
        runtime_path=runtime,
        profile_path=profile,
        workspace_root=workspace,
    )

    assert report.readiness == "READY"
    payload = report.to_dict()
    assert payload["readiness"] == "READY"
    assert any(check["name"] == "ComfyUI startup" and check["status"] == "pass" for check in payload["checks"])
    assert payload["evidence"]["machine"]["swap_used_gib"] == 0.0


def test_doctor_warns_for_active_process_and_dirty_checkout(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / ".comfy-metal"
    for name in ("workloads", "runtimes", "profiles", "comparisons", "results", "logs", "docs"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    runtime = workspace / "runtimes" / "local.toml"
    runtime.write_text('name = "local"\n')
    profile = workspace / "profiles" / "stock.toml"
    profile.write_text('name = "stock"\n')
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    (comfyui / "main.py").write_text("")
    python = comfyui / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")

    monkeypatch.setattr("comfy_metal.doctor._probe_runtime_python", lambda path: {"python": "3.13", "torch": "2.13", "mps_available": True, "error": None})
    monkeypatch.setattr("comfy_metal.doctor._machine_snapshot", lambda: {"system": "Darwin", "platform": "macOS", "machine": "arm64", "chip": "Apple Test", "memory_free_percent": None, "swap_used_gib": 0.0, "load_average": None, "thermal_state": "no-recorded-warning", "power_source": "AC Power"})
    monkeypatch.setattr("comfy_metal.doctor._active_processes", lambda: [{"pid": 123, "command": "ComfyUI/main.py"}])
    monkeypatch.setattr("comfy_metal.doctor._git_dirty", lambda path: True)
    monkeypatch.setattr("comfy_metal.doctor._startup_probe", lambda **kwargs: {"startup_s": 1.0, "system_stats": {}, "mps_telemetry": {"available": True}})

    report = run_doctor(comfyui_root=comfyui, runtime_path=runtime, profile_path=profile, workspace_root=workspace)

    assert report.readiness == "WARN"
    warnings = [check for check in report.checks if check.status == "warn"]
    assert {check.name for check in warnings} >= {"Existing benchmark processes", "ComfyUI Git"}


def test_doctor_blocks_when_mps_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / ".comfy-metal"
    for name in ("workloads", "runtimes", "profiles", "comparisons", "results", "logs", "docs"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    runtime = workspace / "runtimes" / "local.toml"
    runtime.write_text('name = "local"\n')
    profile = workspace / "profiles" / "stock.toml"
    profile.write_text('name = "stock"\n')
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    (comfyui / "main.py").write_text("")
    python = comfyui / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")

    monkeypatch.setattr("comfy_metal.doctor._probe_runtime_python", lambda path: {"python": "3.13", "torch": "2.13", "mps_available": False, "error": None})
    monkeypatch.setattr("comfy_metal.doctor._machine_snapshot", lambda: {"system": "Darwin", "platform": "macOS", "machine": "arm64", "chip": "Apple Test", "memory_free_percent": None, "swap_used_gib": 0.0, "load_average": None, "thermal_state": "no-recorded-warning", "power_source": "AC Power"})
    monkeypatch.setattr("comfy_metal.doctor._active_processes", lambda: [])
    monkeypatch.setattr("comfy_metal.doctor._git_dirty", lambda path: False)

    called = False
    def startup(**kwargs):
        nonlocal called
        called = True
        return {"startup_s": 1.0, "system_stats": {}, "mps_telemetry": {"available": True}}
    monkeypatch.setattr("comfy_metal.doctor._startup_probe", startup)

    report = run_doctor(comfyui_root=comfyui, runtime_path=runtime, profile_path=profile, workspace_root=workspace)

    assert report.readiness == "BLOCKED"
    assert called is False
    assert any(check.name == "MPS" and check.status == "fail" for check in report.checks)


def test_doctor_warns_on_preexisting_swap_without_blocking(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / ".comfy-metal"
    for name in ("workloads", "runtimes", "profiles", "comparisons", "results", "logs", "docs"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    runtime = workspace / "runtimes" / "local.toml"
    runtime.write_text('name = "local"\n')
    profile = workspace / "profiles" / "stock.toml"
    profile.write_text('name = "stock"\n')
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    (comfyui / "main.py").write_text("")
    python = comfyui / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")

    monkeypatch.setattr("comfy_metal.doctor._probe_runtime_python", lambda path: {"python": "3.13", "torch": "2.13", "mps_available": True, "error": None})
    monkeypatch.setattr("comfy_metal.doctor._machine_snapshot", lambda: {"system": "Darwin", "platform": "macOS", "machine": "arm64", "chip": "Apple Test", "memory_free_percent": 70.0, "swap_used_gib": 1.0, "load_average": [1.0, 1.0, 1.0], "thermal_state": "no-recorded-warning", "power_source": "AC Power"})
    monkeypatch.setattr("comfy_metal.doctor._active_processes", lambda: [])
    monkeypatch.setattr("comfy_metal.doctor._git_dirty", lambda path: False)
    monkeypatch.setattr("comfy_metal.doctor._startup_probe", lambda **kwargs: {"startup_s": 1.0, "system_stats": {}, "mps_telemetry": {"available": True}})

    report = run_doctor(comfyui_root=comfyui, runtime_path=runtime, profile_path=profile, workspace_root=workspace)

    assert report.readiness == "WARN"
    assert any(check.name == "Machine state" and check.status == "warn" for check in report.checks)
    assert not any(check.status == "fail" for check in report.checks)
