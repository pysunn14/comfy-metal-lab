from pathlib import Path

import pytest

from comfy_metal.config import load_comparison, load_profile, load_runtime, load_workload


def _workflow(path: Path) -> None:
    path.write_text('{"7": {"class_type": "KSampler", "inputs": {"seed": 42}}}')


def test_load_workload_resolves_generic_session_and_output_contract(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow_api.json"
    _workflow(workflow)
    config = tmp_path / "workload.toml"
    config.write_text(
        'name = "apple"\n'
        'workflow = "workflow_api.json"\n\n'
        '[[session.mutations]]\n'
        'node = "7"\n'
        'input = "seed"\n'
        'cold = 42\n'
        'warm = 43\n\n'
        '[output]\n'
        'node = "9"\n'
        'index = 0\n'
    )

    loaded = load_workload(config)

    assert loaded.name == "apple"
    assert loaded.workflow == workflow
    assert loaded.session is not None
    assert loaded.session.mutations[0].node == "7"
    assert loaded.session.mutations[0].input == "seed"
    assert loaded.session.mutations[0].cold == 42
    assert loaded.session.mutations[0].warm == 43
    assert loaded.output is not None
    assert loaded.output.node == "9"
    assert loaded.output.index == 0


def test_load_workload_supports_nested_json_mutation(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow_api.json"
    _workflow(workflow)
    config = tmp_path / "workload.toml"
    config.write_text(
        'name = "aio"\nworkflow = "workflow_api.json"\n\n'
        '[[session.mutations]]\n'
        'node = "4"\ninput = "generation_settings"\n'
        'path = "sampler.seed"\nformat = "json"\n'
        'cold = 42\nwarm = 43\n\n'
        '[output]\nnode = "5"\n'
    )

    loaded = load_workload(config)
    mutation = loaded.require_session().mutations[0]

    assert mutation.path == "sampler.seed"
    assert mutation.format == "json"


def test_workload_session_is_optional_for_single_run(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow_api.json"
    _workflow(workflow)
    config = tmp_path / "workload.toml"
    config.write_text('name = "apple"\nworkflow = "workflow_api.json"\n')

    loaded = load_workload(config)

    assert loaded.session is None
    assert loaded.output is None
    with pytest.raises(ValueError, match="session"):
        loaded.require_session()
    with pytest.raises(ValueError, match="output"):
        loaded.require_output()


def test_json_mutation_requires_path(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow_api.json"
    _workflow(workflow)
    config = tmp_path / "workload.toml"
    config.write_text(
        'name = "bad"\nworkflow = "workflow_api.json"\n\n'
        '[[session.mutations]]\nnode = "4"\ninput = "settings"\n'
        'format = "json"\ncold = 42\nwarm = 43\n'
    )

    with pytest.raises(ValueError, match="requires mutation path"):
        load_workload(config)


def test_load_profile_contains_only_experiment_server_options(tmp_path: Path) -> None:
    config = tmp_path / "profile.toml"
    config.write_text(
        'name = "metal-flash"\n'
        'server_args = ["--use-flash-attention"]\n'
    )

    loaded = load_profile(config)

    assert loaded.name == "metal-flash"
    assert loaded.server_args == ("--use-flash-attention",)


def test_profile_rejects_machine_specific_runtime_fields(tmp_path: Path) -> None:
    config = tmp_path / "profile.toml"
    config.write_text('name = "bad"\nbase_directory = "/models"\n')

    with pytest.raises(ValueError, match="runtime config"):
        load_profile(config)


def test_load_comparison_quality_threshold(tmp_path: Path) -> None:
    path = tmp_path / "comparison.toml"
    path.write_text(
        'name = "stock-vs-metal"\n\n[quality]\nmetric = "ssim"\nmin_ssim = 0.90\n'
    )

    config = load_comparison(path)

    assert config.name == "stock-vs-metal"
    assert config.quality_metric == "ssim"
    assert config.min_ssim == 0.90


def test_comparison_defaults_to_ssim_point_nine(tmp_path: Path) -> None:
    path = tmp_path / "comparison.toml"
    path.write_text('name = "stock-vs-metal"\n')

    config = load_comparison(path)

    assert config.quality_metric == "ssim"
    assert config.min_ssim == 0.90



def test_load_workload_supports_static_overrides(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow_api.json"
    workflow.write_text('{"12":{"class_type":"FeaturePatch","inputs":{"enabled":true}}}')
    config = tmp_path / "workload.toml"
    config.write_text(
        'name = "variant"\nworkflow = "workflow_api.json"\n\n'
        '[[overrides]]\nnode = "12"\ninput = "enabled"\nvalue = false\n'
    )

    loaded = load_workload(config)

    assert len(loaded.overrides) == 1
    assert loaded.overrides[0].to_dict() == {
        "node": "12", "input": "enabled", "value": False, "format": "value"
    }


def test_load_workload_supports_nested_json_override(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow_api.json"
    workflow.write_text('{"4":{"class_type":"AIO","inputs":{"settings":"{}"}}}')
    config = tmp_path / "workload.toml"
    config.write_text(
        'name = "variant"\nworkflow = "workflow_api.json"\n\n'
        '[[overrides]]\nnode = "4"\ninput = "settings"\n'
        'path = "sampler.cfg"\nformat = "json"\nvalue = 4.0\n'
    )

    override = load_workload(config).overrides[0]

    assert override.path == "sampler.cfg"
    assert override.format == "json"
    assert override.value == 4.0


def test_workload_rejects_override_and_session_mutation_on_same_target(tmp_path: Path) -> None:
    workflow = tmp_path / "workflow_api.json"
    _workflow(workflow)
    config = tmp_path / "workload.toml"
    config.write_text(
        'name = "bad"\nworkflow = "workflow_api.json"\n\n'
        '[[overrides]]\nnode = "7"\ninput = "seed"\nvalue = 1\n\n'
        '[[session.mutations]]\nnode = "7"\ninput = "seed"\ncold = 42\nwarm = 43\n'
    )

    with pytest.raises(ValueError, match="both override and session mutation"):
        load_workload(config)
