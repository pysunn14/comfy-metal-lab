from pathlib import Path

import pytest

from comfy_metal.config import load_runtime, load_workload
from comfy_metal.runtime_requirements import check_required_nodes


def test_runtime_config_owns_machine_specific_model_paths(tmp_path: Path) -> None:
    config = tmp_path / "runtime.toml"
    config.write_text(
        'name = "local-comfyui"\n'
        'comfyui_root = "/runtime/ComfyUI"\n'
        'base_directory = "/models/comfy"\n'
        'python = "/runtime/python"\n'
        'extra_model_paths = ["/extra/a.yaml", "/extra/b.yaml"]\n'
        'server_args = ["--preview-method", "none"]\n'
    )

    runtime = load_runtime(config)

    assert runtime.name == "local-comfyui"
    assert runtime.comfyui_root == Path("/runtime/ComfyUI")
    assert runtime.base_directory == Path("/models/comfy")
    assert runtime.python == Path("/runtime/python")
    assert runtime.extra_model_paths == (Path("/extra/a.yaml"), Path("/extra/b.yaml"))
    assert runtime.server_args == ("--preview-method", "none")


def test_workload_declares_required_comfyui_nodes(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow_api.json"
    workflow.write_text('{"1":{"class_type":"SpecialSampler","inputs":{}}}')
    manifest = tmp_path / "workload.toml"
    manifest.write_text(
        'name = "special"\nworkflow = "workflow_api.json"\n\n'
        '[requirements]\nnodes = ["SpecialSampler", "SaveImage"]\n'
    )

    workload = load_workload(manifest)

    assert workload.requirements.nodes == ("SpecialSampler", "SaveImage")
    assert workload.requirements.to_dict() == {"nodes": ["SpecialSampler", "SaveImage"]}


def test_required_node_check_returns_structured_evidence() -> None:
    evidence = check_required_nodes(
        object_info={"KSampler": {}, "SaveImage": {}},
        required_nodes=("KSampler", "SaveImage"),
    )

    assert evidence == {
        "required_nodes": ["KSampler", "SaveImage"],
        "missing_nodes": [],
        "passed": True,
    }


def test_required_node_check_fails_with_missing_node_names() -> None:
    with pytest.raises(RuntimeError, match="DiTSpectrumPatchAdvanced"):
        check_required_nodes(
            object_info={"KSampler": {}, "SaveImage": {}},
            required_nodes=("KSampler", "DiTSpectrumPatchAdvanced"),
        )


def test_workflow_node_types_are_implicit_runtime_requirements() -> None:
    from comfy_metal.runtime_requirements import required_node_types

    workflow = {
        "1": {"class_type": "UNETLoader", "inputs": {}},
        "12": {"class_type": "DiTSpectrumPatchAdvanced", "inputs": {}},
        "13": {"class_type": "KSampler", "inputs": {}},
        "15": {"class_type": "SaveImage", "inputs": {}},
    }

    assert required_node_types(workflow, declared_nodes=("ExtraCapability",)) == (
        "DiTSpectrumPatchAdvanced",
        "ExtraCapability",
        "KSampler",
        "SaveImage",
        "UNETLoader",
    )
