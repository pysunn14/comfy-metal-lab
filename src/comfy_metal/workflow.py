"""Validation and explicit transformations for API-format ComfyUI workflows."""

from __future__ import annotations

import copy
import json
from typing import Any

from .config import MutationConfig, OverrideConfig


def validate_api_workflow(workflow: dict[str, Any]) -> None:
    """Reject the UI/save format and malformed API-format workflow objects."""

    if not workflow:
        raise ValueError("workflow JSON must contain at least one API node")
    if "nodes" in workflow and isinstance(workflow.get("nodes"), list):
        raise ValueError(
            "workflow appears to use ComfyUI UI/save format; export or save it in API format"
        )
    for node_id, node in workflow.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            raise ValueError("API workflow nodes must be keyed by string node IDs")
        if not isinstance(node.get("class_type"), str):
            raise ValueError(f"API workflow node {node_id} is missing class_type")
        if not isinstance(node.get("inputs"), dict):
            raise ValueError(f"API workflow node {node_id} is missing inputs")


def _path_segments(path: str) -> list[str]:
    segments = path.split(".")
    if not segments or any(not segment for segment in segments):
        raise ValueError(f"invalid mutation path: {path!r}")
    return segments


def _set_nested_value(root: Any, path: str, value: Any) -> None:
    current = root
    segments = _path_segments(path)
    for segment in segments[:-1]:
        if isinstance(current, dict):
            if segment not in current:
                raise ValueError(f"mutation path does not exist: {path!r}")
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if index >= len(current):
                raise ValueError(f"mutation path list index is out of range: {path!r}")
            current = current[index]
        else:
            raise ValueError(f"mutation path cannot traverse {segment!r}: {path!r}")

    leaf = segments[-1]
    if isinstance(current, dict):
        if leaf not in current:
            raise ValueError(f"mutation path does not exist: {path!r}")
        current[leaf] = value
        return
    if isinstance(current, list) and leaf.isdigit():
        index = int(leaf)
        if index >= len(current):
            raise ValueError(f"mutation path list index is out of range: {path!r}")
        current[index] = value
        return
    raise ValueError(f"mutation path cannot set {leaf!r}: {path!r}")


def _set_workflow_input(
    workflow: dict[str, Any],
    *,
    node_id: str,
    input_name: str,
    value: Any,
    path: str | None,
    format: str,
    label: str,
) -> None:
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        raise ValueError(f"{label} node does not exist: {node_id}")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict) or input_name not in inputs:
        raise ValueError(f"{label} input does not exist: node {node_id} inputs.{input_name}")

    if format == "value":
        inputs[input_name] = value
        return
    if format != "json":
        raise ValueError(f"unsupported {label} format: {format!r}")

    raw = inputs[input_name]
    if not isinstance(raw, str):
        raise ValueError(f"JSON {label} input must be a string: node {node_id} inputs.{input_name}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON {label} input is not valid JSON: node {node_id} inputs.{input_name}"
        ) from exc
    if path is None:
        raise ValueError(f"JSON {label} requires a nested path")
    _set_nested_value(parsed, path, value)
    inputs[input_name] = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)


def apply_overrides(
    workflow: dict[str, Any],
    *,
    overrides: tuple[OverrideConfig, ...],
) -> dict[str, Any]:
    """Clone an API workflow and apply workload-static input overrides."""

    changed = copy.deepcopy(workflow)
    validate_api_workflow(changed)
    for override in overrides:
        _set_workflow_input(
            changed,
            node_id=override.node,
            input_name=override.input,
            value=override.value,
            path=override.path,
            format=override.format,
            label="override",
        )
    return changed


def apply_session_mutations(
    workflow: dict[str, Any],
    *,
    mutations: tuple[MutationConfig, ...],
    phase: str,
) -> dict[str, Any]:
    """Clone an API workflow and apply the explicit cold or warm mutation set."""

    if phase not in {"cold", "warm"}:
        raise ValueError(f"unknown generation phase: {phase!r}")
    if not mutations:
        raise ValueError("at least one session mutation is required")

    changed = copy.deepcopy(workflow)
    validate_api_workflow(changed)
    for mutation in mutations:
        _set_workflow_input(
            changed,
            node_id=mutation.node,
            input_name=mutation.input,
            value=mutation.cold if phase == "cold" else mutation.warm,
            path=mutation.path,
            format=mutation.format,
            label="mutation",
        )
    return changed
