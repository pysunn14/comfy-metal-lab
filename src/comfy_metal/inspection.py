"""Workflow inspection and manifest suggestions without model-specific core logic."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from .workflow import validate_api_workflow


@dataclass(frozen=True)
class MutationSuggestion:
    node: str
    class_type: str
    input: str
    cold: int = 42
    warm: int = 43
    path: str | None = None
    format: str = "value"
    detector: str = "generic"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "node": self.node,
            "class_type": self.class_type,
            "input": self.input,
            "cold": self.cold,
            "warm": self.warm,
            "format": self.format,
            "detector": self.detector,
        }
        if self.path is not None:
            payload["path"] = self.path
        return payload


@dataclass(frozen=True)
class OutputSuggestion:
    node: str
    class_type: str
    index: int = 0
    detector: str = "generic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "class_type": self.class_type,
            "index": self.index,
            "detector": self.detector,
        }


class WorkflowAdapter(Protocol):
    """Optional compatibility knowledge layered above the generic inspector."""

    name: str

    def mutation_suggestions(self, workflow: dict[str, Any]) -> Iterable[MutationSuggestion]: ...

    def output_suggestions(self, workflow: dict[str, Any]) -> Iterable[OutputSuggestion]: ...


@dataclass(frozen=True)
class WorkflowInspection:
    workflow: Path
    nodes: int
    mutations: tuple[MutationSuggestion, ...]
    outputs: tuple[OutputSuggestion, ...]

    @property
    def can_autogenerate(self) -> bool:
        return len(self.mutations) == 1 and len(self.outputs) == 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": str(self.workflow),
            "nodes": self.nodes,
            "mutations": [item.to_dict() for item in self.mutations],
            "outputs": [item.to_dict() for item in self.outputs],
            "can_autogenerate": self.can_autogenerate,
        }


def _walk_seed_paths(value: Any, prefix: tuple[str, ...] = ()) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            path = (*prefix, str(key))
            if key in {"seed", "noise_seed"} and isinstance(child, int) and not isinstance(child, bool):
                yield ".".join(path)
            yield from _walk_seed_paths(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_seed_paths(child, (*prefix, str(index)))


def _generic_mutations(workflow: dict[str, Any]) -> list[MutationSuggestion]:
    suggestions: list[MutationSuggestion] = []
    for node_id, node in workflow.items():
        class_type = str(node["class_type"])
        inputs = node["inputs"]
        for input_name in ("seed", "noise_seed"):
            value = inputs.get(input_name)
            if isinstance(value, int) and not isinstance(value, bool):
                suggestions.append(
                    MutationSuggestion(
                        node=node_id,
                        class_type=class_type,
                        input=input_name,
                        detector="direct-seed-input",
                    )
                )
        for input_name, raw in inputs.items():
            if not isinstance(raw, str) or not raw.lstrip().startswith(("{", "[")):
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for path in _walk_seed_paths(parsed):
                suggestions.append(
                    MutationSuggestion(
                        node=node_id,
                        class_type=class_type,
                        input=str(input_name),
                        path=path,
                        format="json",
                        detector="nested-json-seed",
                    )
                )
    # Exact target de-duplication keeps adapter additions deterministic later.
    unique: dict[tuple[str, str, str | None], MutationSuggestion] = {}
    for suggestion in suggestions:
        unique.setdefault((suggestion.node, suggestion.input, suggestion.path), suggestion)
    return list(unique.values())


def _generic_outputs(workflow: dict[str, Any]) -> list[OutputSuggestion]:
    return [
        OutputSuggestion(node=node_id, class_type=str(node["class_type"]), detector="save-image")
        for node_id, node in workflow.items()
        if node.get("class_type") == "SaveImage" and "images" in node.get("inputs", {})
    ]


def inspect_workflow(
    path: Path,
    *,
    adapters: tuple[WorkflowAdapter, ...] = (),
) -> WorkflowInspection:
    parsed = json.loads(path.read_text())
    if not isinstance(parsed, dict):
        raise ValueError("workflow JSON must contain an object")
    validate_api_workflow(parsed)

    mutations = _generic_mutations(parsed)
    outputs = _generic_outputs(parsed)
    for adapter in adapters:
        mutations.extend(adapter.mutation_suggestions(parsed))
        outputs.extend(adapter.output_suggestions(parsed))

    mutation_map = {(item.node, item.input, item.path): item for item in mutations}
    output_map = {(item.node, item.index): item for item in outputs}
    return WorkflowInspection(
        workflow=path,
        nodes=len(parsed),
        mutations=tuple(mutation_map.values()),
        outputs=tuple(output_map.values()),
    )


def _toml_scalar(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_workload_toml(*, name: str, workflow_name: str, inspection: WorkflowInspection) -> str:
    if not inspection.can_autogenerate:
        raise ValueError(
            "workflow inspection is ambiguous; select mutation/output targets explicitly before writing a manifest"
        )
    mutation = inspection.mutations[0]
    output = inspection.outputs[0]
    lines = [
        f"name = {_toml_scalar(name)}",
        f"workflow = {_toml_scalar(workflow_name)}",
        "",
        "[[session.mutations]]",
        f"node = {_toml_scalar(mutation.node)}",
        f"input = {_toml_scalar(mutation.input)}",
    ]
    if mutation.path is not None:
        lines.append(f"path = {_toml_scalar(mutation.path)}")
    if mutation.format != "value":
        lines.append(f"format = {_toml_scalar(mutation.format)}")
    lines.extend(
        [
            f"cold = {_toml_scalar(mutation.cold)}",
            f"warm = {_toml_scalar(mutation.warm)}",
            "",
            "[output]",
            f"node = {_toml_scalar(output.node)}",
            f"index = {output.index}",
            "",
        ]
    )
    return "\n".join(lines)
