from __future__ import annotations

import json

import pytest

from comfy_metal.config import MutationConfig, OverrideConfig
from comfy_metal.workflow import apply_overrides, apply_session_mutations, validate_api_workflow


def test_plain_mutation_clones_and_patches_declared_input() -> None:
    source = {
        "7": {"class_type": "KSampler", "inputs": {"seed": 42, "steps": 8}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
    }
    mutations = (MutationConfig(node="7", input="seed", cold=42, warm=43),)

    changed = apply_session_mutations(source, mutations=mutations, phase="warm")

    assert changed["7"]["inputs"]["seed"] == 43
    assert source["7"]["inputs"]["seed"] == 42
    assert changed["7"]["inputs"]["steps"] == 8


def test_nested_json_mutation_changes_only_declared_path() -> None:
    settings = json.dumps({"sampler": {"seed": 10, "steps": 30}, "other": {"seed": 999}})
    source = {
        "4": {"class_type": "EasyUseAIO", "inputs": {"generation_settings": settings}},
    }
    mutations = (
        MutationConfig(
            node="4",
            input="generation_settings",
            path="sampler.seed",
            format="json",
            cold=42,
            warm=43,
        ),
    )

    changed = apply_session_mutations(source, mutations=mutations, phase="cold")
    parsed = json.loads(changed["4"]["inputs"]["generation_settings"])

    assert parsed["sampler"]["seed"] == 42
    assert parsed["sampler"]["steps"] == 30
    assert parsed["other"]["seed"] == 999


def test_mutation_rejects_missing_input() -> None:
    source = {"7": {"class_type": "KSampler", "inputs": {"steps": 8}}}
    mutations = (MutationConfig(node="7", input="seed", cold=42, warm=43),)

    with pytest.raises(ValueError, match="mutation input does not exist"):
        apply_session_mutations(source, mutations=mutations, phase="warm")


def test_ui_save_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="API format"):
        validate_api_workflow({"nodes": [], "links": []})



def test_plain_override_clones_and_patches_declared_input() -> None:
    source = {
        "12": {"class_type": "FeaturePatch", "inputs": {"enabled": True, "strength": 1.0}},
    }
    overrides = (OverrideConfig(node="12", input="enabled", value=False),)

    changed = apply_overrides(source, overrides=overrides)

    assert changed["12"]["inputs"]["enabled"] is False
    assert changed["12"]["inputs"]["strength"] == 1.0
    assert source["12"]["inputs"]["enabled"] is True


def test_nested_json_override_changes_only_declared_path() -> None:
    settings = json.dumps({"sampler": {"cfg": 3.0, "steps": 30}, "other": {"cfg": 9.0}})
    source = {"4": {"class_type": "AIO", "inputs": {"generation_settings": settings}}}
    overrides = (
        OverrideConfig(
            node="4", input="generation_settings", value=4.0,
            path="sampler.cfg", format="json",
        ),
    )

    changed = apply_overrides(source, overrides=overrides)
    parsed = json.loads(changed["4"]["inputs"]["generation_settings"])

    assert parsed["sampler"]["cfg"] == 4.0
    assert parsed["sampler"]["steps"] == 30
    assert parsed["other"]["cfg"] == 9.0
