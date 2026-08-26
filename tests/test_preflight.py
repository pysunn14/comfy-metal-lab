from pathlib import Path

from comfy_metal.preflight import run_preflight


def _configs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    workflow_dir = tmp_path / "workload"
    workflow_dir.mkdir()
    workflow = workflow_dir / "workflow_api.json"
    workflow.write_text(
        '{"1":{"class_type":"UNETLoader","inputs":{}},'
        '"12":{"class_type":"DiTSpectrumPatchAdvanced","inputs":{}},'
        '"15":{"class_type":"SaveImage","inputs":{}}}'
    )
    workload = workflow_dir / "workload.toml"
    workload.write_text('name = "anima"\nworkflow = "workflow_api.json"\n')
    runtime = tmp_path / "runtime.toml"
    runtime.write_text('name = "local"\nbase_directory = "/models"\n')
    profile = tmp_path / "profile.toml"
    profile.write_text('name = "stock"\n')
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    return workload, runtime, profile, comfyui


def test_preflight_checks_implicit_workflow_node_requirements(tmp_path: Path, monkeypatch) -> None:
    workload, runtime, profile, comfyui = _configs(tmp_path)

    class FakeProcess:
        def __init__(self, config):
            self.config = config
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def wait_until_ready(self):
            return 1.25

    class FakeClient:
        def __init__(self, *, host, port):
            pass
        def object_info(self):
            return {
                "UNETLoader": {},
                "DiTSpectrumPatchAdvanced": {},
                "SaveImage": {},
            }

    monkeypatch.setattr("comfy_metal.preflight.ComfyUIProcess", FakeProcess)
    monkeypatch.setattr("comfy_metal.preflight.ComfyUIClient", FakeClient)

    report = run_preflight(
        comfyui_root=comfyui,
        workload_path=workload,
        runtime_path=runtime,
        profile_path=profile,
    )

    assert report["status"] == "passed"
    assert report["workload"] == "anima"
    assert report["runtime"] == "local"
    assert report["profile"] == "stock"
    assert report["server_startup_s"] == 1.25
    assert report["runtime_preflight"]["passed"] is True
    assert "DiTSpectrumPatchAdvanced" in report["runtime_preflight"]["required_nodes"]



def test_preflight_validates_static_override_before_starting_comfyui(tmp_path: Path, monkeypatch) -> None:
    workload, runtime, profile, comfyui = _configs(tmp_path)
    workload.write_text(
        'name = "variant"\nworkflow = "workflow_api.json"\n\n'
        '[[overrides]]\nnode = "12"\ninput = "missing"\nvalue = false\n'
    )

    started = False

    class FailIfStarted:
        def __init__(self, config):
            nonlocal started
            started = True

    monkeypatch.setattr("comfy_metal.preflight.ComfyUIProcess", FailIfStarted)

    import pytest
    with pytest.raises(ValueError, match="override input does not exist"):
        run_preflight(
            comfyui_root=comfyui,
            workload_path=workload,
            runtime_path=runtime,
            profile_path=profile,
        )

    assert started is False
