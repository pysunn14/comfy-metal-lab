"""Runtime capability checks performed before an expensive generation begins."""

from __future__ import annotations

from typing import Any


def check_required_nodes(
    *,
    object_info: dict[str, Any],
    required_nodes: tuple[str, ...],
) -> dict[str, Any]:
    """Validate that ComfyUI exposes every node required by the workload."""

    missing = [name for name in required_nodes if name not in object_info]
    evidence = {
        "required_nodes": list(required_nodes),
        "missing_nodes": missing,
        "passed": not missing,
    }
    if missing:
        raise RuntimeError("missing required ComfyUI nodes: " + ", ".join(missing))
    return evidence


def required_node_types(
    workflow: dict[str, Any],
    *,
    declared_nodes: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Return the stable set of node classes required to execute a workflow."""

    node_types: set[str] = set(declared_nodes)
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if isinstance(class_type, str) and class_type:
            node_types.add(class_type)
    return tuple(sorted(node_types))
