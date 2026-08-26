from __future__ import annotations

import json
from pathlib import Path

from comfy_metal.cli import main
from comfy_metal.workspace import init_workspace


def _managed_workload(root: Path) -> None:
    directory = root / "workloads" / "demo"
    directory.mkdir(parents=True)
    (directory / "workflow_api.json").write_text(
        json.dumps({
            "7": {"class_type": "KSampler", "inputs": {"seed": 42}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
        })
    )
    (directory / "workload.toml").write_text(
        'name = "demo"\nworkflow = "workflow_api.json"\n\n'
        '[[session.mutations]]\nnode = "7"\ninput = "seed"\ncold = 42\nwarm = 43\n\n'
        '[output]\nnode = "9"\nindex = 0\n'
    )


def test_cli_init_creates_default_managed_configs(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / ".comfy-metal"

    assert main(["init", "--workspace", str(workspace)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace"] == str(workspace)
    assert (workspace / "runtimes" / "local.toml").is_file()
    assert (workspace / "profiles" / "stock.toml").is_file()


def test_cli_bench_resolves_short_names_and_allocates_managed_output(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / ".comfy-metal"
    init_workspace(workspace)
    _managed_workload(workspace)
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    captured = {}

    def fake_benchmark(**kwargs):
        captured.update(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr("comfy_metal.cli.run_benchmark", fake_benchmark)

    assert main([
        "bench",
        "--workspace", str(workspace),
        "--comfyui-root", str(comfyui),
        "--workload", "demo",
        "--runtime", "local",
        "--profile", "stock",
        "--sessions", "2",
    ]) == 0

    assert captured["workload_path"] == workspace / "workloads" / "demo" / "workload.toml"
    assert captured["runtime_path"] == workspace / "runtimes" / "local.toml"
    assert captured["profile_path"] == workspace / "profiles" / "stock.toml"
    assert captured["output_dir"] == workspace / "results" / "demo" / "stock" / "bench-001"
    assert captured["sessions"] == 2
    stderr = capsys.readouterr().err
    assert "bench-001" in stderr


def test_cli_doctor_resolves_managed_defaults(tmp_path: Path, monkeypatch, capsys) -> None:
    from comfy_metal.doctor import DoctorCheck, DoctorReport

    workspace = tmp_path / ".comfy-metal"
    init_workspace(workspace)
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    captured = {}

    def fake_doctor(**kwargs):
        captured.update(kwargs)
        return DoctorReport((DoctorCheck("MPS", "pass", "available"),), {"ok": True})

    monkeypatch.setattr("comfy_metal.cli.run_doctor", fake_doctor)

    assert main([
        "doctor",
        "--workspace", str(workspace),
        "--comfyui-root", str(comfyui),
    ]) == 0

    assert captured["runtime_path"] == workspace / "runtimes" / "local.toml"
    assert captured["profile_path"] == workspace / "profiles" / "stock.toml"
    assert "Benchmark readiness: READY" in capsys.readouterr().out


def test_cli_doctor_returns_two_when_blocked(tmp_path: Path, monkeypatch, capsys) -> None:
    from comfy_metal.doctor import DoctorCheck, DoctorReport

    workspace = tmp_path / ".comfy-metal"
    init_workspace(workspace)
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    monkeypatch.setattr(
        "comfy_metal.cli.run_doctor",
        lambda **kwargs: DoctorReport((DoctorCheck("MPS", "fail", "unavailable"),)),
    )

    assert main([
        "doctor", "--workspace", str(workspace), "--comfyui-root", str(comfyui), "--json"
    ]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["readiness"] == "BLOCKED"
