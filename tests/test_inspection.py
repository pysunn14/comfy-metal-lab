import json
from pathlib import Path

import pytest

from comfy_metal.inspection import inspect_workflow, render_workload_toml


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload))
    return path


def test_inspector_detects_standard_sampler_and_save_image(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "workflow.json",
        {
            "7": {"class_type": "KSampler", "inputs": {"seed": 123, "steps": 8}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
        },
    )

    result = inspect_workflow(path)

    assert result.can_autogenerate is True
    assert result.mutations[0].node == "7"
    assert result.mutations[0].input == "seed"
    assert result.outputs[0].node == "9"
    manifest = render_workload_toml(name="apple", workflow_name="workflow.json", inspection=result)
    assert 'input = "seed"' in manifest
    assert '[output]' in manifest


def test_inspector_detects_flux_style_random_noise(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "flux.json",
        {
            "25": {"class_type": "RandomNoise", "inputs": {"noise_seed": 99}},
            "40": {"class_type": "SaveImage", "inputs": {"images": ["39", 0]}},
        },
    )

    result = inspect_workflow(path)

    assert result.mutations[0].input == "noise_seed"
    assert result.mutations[0].detector == "direct-seed-input"


def test_inspector_detects_nested_json_seed(tmp_path: Path) -> None:
    settings = json.dumps({"sampler": {"seed": 123, "steps": 30}})
    path = _write(
        tmp_path / "aio.json",
        {
            "4": {"class_type": "EasyUseAIO", "inputs": {"generation_settings": settings}},
            "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
        },
    )

    result = inspect_workflow(path)

    mutation = result.mutations[0]
    assert mutation.input == "generation_settings"
    assert mutation.path == "sampler.seed"
    assert mutation.format == "json"


def test_inspector_refuses_ambiguous_manifest_generation(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "two-stage.json",
        {
            "1": {"class_type": "KSamplerAdvanced", "inputs": {"noise_seed": 1}},
            "2": {"class_type": "KSamplerAdvanced", "inputs": {"noise_seed": 1}},
            "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
        },
    )

    result = inspect_workflow(path)

    assert result.can_autogenerate is False
    with pytest.raises(ValueError, match="ambiguous"):
        render_workload_toml(name="two-stage", workflow_name="two-stage.json", inspection=result)
