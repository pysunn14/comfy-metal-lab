import json
from pathlib import Path

import pytest

from comfy_metal.orchestrator import WorkerExecution
from comfy_metal.run import run_once, run_session


def _configs(tmp_path: Path, *, with_session: bool) -> tuple[Path, Path, Path, Path]:
    workload_dir = tmp_path / "workload"
    workload_dir.mkdir()
    (workload_dir / "workflow_api.json").write_text(
        '{"7": {"class_type": "KSampler", "inputs": {"seed": 42}}, '
        '"9": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}}}'
    )
    workload_file = workload_dir / "workload.toml"
    session = (
        '\n[[session.mutations]]\nnode = "7"\ninput = "seed"\ncold = 42\nwarm = 43\n'
        '\n[output]\nnode = "9"\nindex = 0\n'
        if with_session else ""
    )
    workload_file.write_text(
        'name = "apple"\nworkflow = "workflow_api.json"\n'
        '[[overrides]]\nnode = "7"\ninput = "steps"\nvalue = 12\n'
        + session
    )
    runtime_file = tmp_path / "runtime.toml"
    runtime_file.write_text(
        'name = "local"\nbase_directory = "/models"\n'
        'server_args = ["--disable-all-custom-nodes"]\n'
    )
    profile_file = tmp_path / "profile.toml"
    profile_file.write_text('name = "stock"\nserver_args = ["--use-pytorch-cross-attention"]\n')
    comfy_root = tmp_path / "ComfyUI"
    comfy_root.mkdir()
    return workload_file, runtime_file, profile_file, comfy_root


def test_run_once_materializes_single_generation_artifacts(tmp_path: Path, monkeypatch) -> None:
    workload_file, runtime_file, profile_file, comfy_root = _configs(tmp_path, with_session=False)
    output_dir = tmp_path / "run"

    def fake_run_worker_process(command, *, label):
        assert command[2] == "comfy_metal.worker"
        assert "single" in command
        assert "--server-arg=--disable-all-custom-nodes" in command
        assert "--server-arg=--use-pytorch-cross-attention" in command
        assert command[command.index("--base-directory") + 1] == "/models"
        override = json.loads(command[command.index("--override") + 1])
        assert override == {"node": "7", "input": "steps", "value": 12, "format": "value"}
        image_arg = Path(command[command.index("--output-image") + 1])
        image_arg.parent.mkdir(parents=True, exist_ok=True)
        image_arg.write_bytes(b"png")
        return WorkerExecution(
            label=label,
            result={
                "condition": "stock",
                "rep": 0,
                "elapsed_s": 12.5,
                "peak_memory_mb": None,
                "metrics": {"server_startup_s": 3.0, "output_image": str(image_arg)},
            },
            stdout="worker stdout\n",
            stderr="worker stderr\n",
        )

    monkeypatch.setattr("comfy_metal.run.run_worker_process", fake_run_worker_process)
    report = run_once(
        comfyui_root=comfy_root,
        workload_path=workload_file,
        runtime_path=runtime_file,
        profile_path=profile_file,
        output_dir=output_dir,
    )

    assert report["schema_version"] == 2
    assert report["result"]["elapsed_s"] == 12.5
    assert report["overrides"] == [{"node": "7", "input": "steps", "value": 12, "format": "value"}]
    assert (output_dir / "image.png").read_bytes() == b"png"
    assert json.loads((output_dir / "report.json").read_text()) == report


def test_run_session_materializes_generic_cold_and_warm_artifacts(tmp_path: Path, monkeypatch) -> None:
    workload_file, runtime_file, profile_file, comfy_root = _configs(tmp_path, with_session=True)
    output_dir = tmp_path / "session"

    def fake_run_worker_process(command, *, label):
        assert "session" in command
        assert command[command.index("--session-index") + 1] == "2"
        assert command[command.index("--output-node") + 1] == "9"
        assert command[command.index("--output-index") + 1] == "0"
        assert "--server-arg=--disable-all-custom-nodes" in command
        assert "--server-arg=--use-pytorch-cross-attention" in command
        assert command[command.index("--base-directory") + 1] == "/models"
        override = json.loads(command[command.index("--override") + 1])
        assert override == {"node": "7", "input": "steps", "value": 12, "format": "value"}
        mutation = json.loads(command[command.index("--mutation") + 1])
        assert mutation == {
            "node": "7", "input": "seed", "cold": 42, "warm": 43, "format": "value"
        }
        cold_path = Path(command[command.index("--cold-output-image") + 1])
        warm_path = Path(command[command.index("--warm-output-image") + 1])
        cold_path.parent.mkdir(parents=True, exist_ok=True)
        cold_path.write_bytes(b"cold")
        warm_path.write_bytes(b"warm")
        return WorkerExecution(
            label=label,
            result={
                "condition": "stock",
                "session": 2,
                "server_startup_s": 3.0,
                "cold": {
                    "phase": "cold", "elapsed_s": 12.0,
                    "peak_memory_mb": 100.0, "metrics": {"output_image": str(cold_path)},
                },
                "warm": {
                    "phase": "warm", "elapsed_s": 5.0,
                    "peak_memory_mb": 90.0, "metrics": {"output_image": str(warm_path)},
                },
            },
            stdout="session stdout\n",
            stderr="session stderr\n",
        )

    monkeypatch.setattr("comfy_metal.run.run_worker_process", fake_run_worker_process)
    report = run_session(
        comfyui_root=comfy_root,
        workload_path=workload_file,
        runtime_path=runtime_file,
        profile_path=profile_file,
        output_dir=output_dir,
        session=2,
    )

    assert report["schema_version"] == 4
    assert report["runtime"] == "local"
    assert report["overrides"] == [{"node": "7", "input": "steps", "value": 12, "format": "value"}]
    assert report["session"]["cold"]["phase"] == "cold"
    assert report["session"]["warm"]["phase"] == "warm"
    assert (output_dir / "cold.png").read_bytes() == b"cold"
    assert (output_dir / "warm.png").read_bytes() == b"warm"
    assert json.loads((output_dir / "report.json").read_text()) == report

    with pytest.raises(FileExistsError):
        run_session(
            comfyui_root=comfy_root,
            workload_path=workload_file,
            runtime_path=runtime_file,
            profile_path=profile_file,
            output_dir=output_dir,
            session=2,
        )
