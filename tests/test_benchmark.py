import json
from pathlib import Path

import pytest

from comfy_metal.benchmark import run_benchmark


def _fixture_configs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    workload_dir = tmp_path / "workload"
    workload_dir.mkdir()
    (workload_dir / "workflow.json").write_text(
        '{"7": {"class_type": "KSampler", "inputs": {"seed": 42, "steps": 8}}, '
        '"9": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}}}'
    )
    workload = workload_dir / "workload.toml"
    workload.write_text(
        'name = "apple"\nworkflow = "workflow.json"\n\n'
        '[[overrides]]\nnode = "7"\ninput = "steps"\nvalue = 12\n\n'
        '[[session.mutations]]\nnode = "7"\ninput = "seed"\ncold = 42\nwarm = 43\n\n'
        '[output]\nnode = "9"\nindex = 0\n'
    )
    runtime = tmp_path / "runtime.toml"
    runtime.write_text('name = "local"\nbase_directory = "/models"\n')
    runtime = tmp_path / "runtime.toml"
    runtime.write_text('name = "local"\n')
    profile = tmp_path / "profile.toml"
    profile.write_text('name = "stock"\n')
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    return workload, runtime, profile, comfyui


def _phase(phase: str, elapsed: float, output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(phase.encode())
    return {
        "phase": phase,
        "elapsed_s": elapsed,
        "peak_memory_mb": 100.0,
        "metrics": {"output_image": str(output)},
    }


def test_benchmark_runs_three_isolated_sessions_and_aggregates(tmp_path: Path, monkeypatch):
    workload, runtime, profile, comfyui = _fixture_configs(tmp_path)
    output = tmp_path / "benchmark"
    seen_sessions: list[int] = []

    snapshots = iter([{"phase": "pre"}, {"phase": "post"}])
    monkeypatch.setattr(
        "comfy_metal.benchmark.collect_environment_snapshot",
        lambda **kwargs: next(snapshots),
    )

    def fake_run_session(*, output_dir, session, runtime_path, **kwargs):
        assert runtime_path == runtime
        seen_sessions.append(session)
        output_dir.mkdir(parents=True)
        result = {
            "condition": "stock",
            "session": session,
            "server_startup_s": [3.0, 2.0, 2.5][session],
            "cold": _phase("cold", [12.0, 10.0, 11.0][session], output_dir / "cold.png"),
            "warm": _phase("warm", [5.0, 4.0, 6.0][session], output_dir / "warm.png"),
        }
        report = {"schema_version": 4, "workload": "apple", "runtime": "local", "profile": "stock", "session": result}
        (output_dir / "report.json").write_text(json.dumps(report))
        return report

    class FakeWatcher:
        def __init__(self, output_path, *, interval_seconds=1.0):
            self.output_path = output_path
            self.session = int(output_path.stem.split("-")[-1])

        def start(self):
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text("sample\n")

        def stop(self):
            return {
                "samples": 1,
                "start_swap_used_gib": 1.0,
                "max_swap_used_gib": 1.0 + self.session * 0.1,
                "swap_growth_gib": self.session * 0.1,
                "path": str(self.output_path),
            }

    monkeypatch.setattr("comfy_metal.benchmark.run_session", fake_run_session)
    monkeypatch.setattr("comfy_metal.benchmark.MacSwapWatcher", FakeWatcher)

    progress: list[str] = []
    report = run_benchmark(
        comfyui_root=comfyui,
        workload_path=workload,
        runtime_path=runtime,
        profile_path=profile,
        output_dir=output,
        sessions=3,
        progress=progress.append,
    )

    assert seen_sessions == [0, 1, 2]
    assert report["status"] == "completed"
    assert report["schema_version"] == 4
    assert report["runtime"] == "local"
    assert report["overrides"] == [{"node": "7", "input": "steps", "value": 12, "format": "value"}]
    assert report["protocol"] == {
        "unit": "session",
        "isolation": "fresh-worker-and-comfyui-per-session",
        "generations": ["cold", "warm"],
    }
    assert report["session_contract"] == {
        "session": {
            "mutations": [
                {"node": "7", "input": "seed", "cold": 42, "warm": 43, "format": "value"}
            ]
        },
        "output": {"node": "9", "index": 0},
        "overrides": [
            {"node": "7", "input": "steps", "value": 12, "format": "value"}
        ],
    }
    assert report["probe_session"] == 0
    assert report["timing"]["cold_generation"]["median_seconds"] == 11.0
    assert report["timing"]["warm_generation"]["median_seconds"] == 5.0
    assert report["completed_sessions"] == 3
    assert report["telemetry"][2]["swap_growth_gib"] == pytest.approx(0.2)
    assert json.loads((output / "report.json").read_text()) == report
    assert any("session 1/3" in message for message in progress)


def test_benchmark_rejects_workload_without_session_contract(tmp_path: Path):
    workload_dir = tmp_path / "workload"
    workload_dir.mkdir()
    (workload_dir / "workflow.json").write_text(
        '{"1": {"class_type": "SaveImage", "inputs": {"images": ["0", 0]}}}'
    )
    workload = workload_dir / "workload.toml"
    workload.write_text('name = "apple"\nworkflow = "workflow.json"\n')
    runtime = tmp_path / "runtime.toml"
    runtime.write_text('name = "local"\n')
    profile = tmp_path / "profile.toml"
    profile.write_text('name = "stock"\n')
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()

    with pytest.raises(ValueError, match="session"):
        run_benchmark(
            comfyui_root=comfyui,
            workload_path=workload,
            runtime_path=runtime,
            profile_path=profile,
            output_dir=tmp_path / "benchmark",
            sessions=1,
        )
