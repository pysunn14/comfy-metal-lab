import hashlib
from pathlib import Path

from comfy_metal.environment import (
    collect_environment_snapshot,
    parse_load_average,
    parse_memory_free_percent,
    thermal_state,
)


def test_environment_parsers_cover_macos_outputs():
    assert parse_memory_free_percent("System-wide memory free percentage: 45%\n") == 45.0
    assert parse_load_average("{ 4.30 3.70 3.56 }") == [4.30, 3.70, 3.56]
    assert thermal_state(
        "Note: No thermal warning level has been recorded\n"
        "Note: No performance warning level has been recorded\n"
    ) == "no-recorded-warning"
    assert thermal_state("CPU_Speed_Limit = 80\n") == "reported"
    assert thermal_state(None) == "unavailable"


def test_environment_snapshot_hashes_benchmark_inputs(tmp_path: Path, monkeypatch):
    workload = tmp_path / "workload.toml"
    workflow = tmp_path / "workflow.json"
    runtime = tmp_path / "runtime.toml"
    profile = tmp_path / "profile.toml"
    workload.write_text('name = "apple"\n')
    workflow.write_text("{}")
    runtime.write_text('name = "local"\n')
    profile.write_text('name = "stock"\n')

    monkeypatch.setattr(
        "comfy_metal.environment._hardware_snapshot",
        lambda: {"chip": "Apple Test", "ram_gib": 64, "machine": "arm64"},
    )
    monkeypatch.setattr(
        "comfy_metal.environment._system_snapshot",
        lambda: {
            "load_average": [1.0, 2.0, 3.0],
            "memory_free_percent": 50.0,
            "thermal": {"state": "no-recorded-warning", "raw": "clear"},
            "power_source": "AC Power",
        },
    )
    monkeypatch.setattr(
        "comfy_metal.environment._runtime_snapshot",
        lambda root: {"python": "3.13", "torch": "2.13.0", "mps_available": True},
    )
    monkeypatch.setattr(
        "comfy_metal.environment._git_snapshot",
        lambda path: {"commit": "abc123", "dirty": False},
    )

    snapshot = collect_environment_snapshot(
        comfyui_root=tmp_path / "ComfyUI",
        workload_path=workload,
        workflow_path=workflow,
        runtime_path=runtime,
        profile_path=profile,
    )

    assert snapshot["hardware"]["chip"] == "Apple Test"
    assert snapshot["runtime"]["torch"] == "2.13.0"
    assert snapshot["comfyui_git"]["commit"] == "abc123"
    assert snapshot["provenance"]["workflow_sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert snapshot["provenance"]["runtime_sha256"] == hashlib.sha256(b'name = "local"\n').hexdigest()
    assert snapshot["system"]["thermal"]["state"] == "no-recorded-warning"
