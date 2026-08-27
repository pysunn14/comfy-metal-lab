"""TOML-backed configuration types for workloads, profiles, and comparisons."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Scalar = str | int | float | bool
ComparisonFactor = Literal["workload", "runtime", "profile"]


@dataclass(frozen=True)
class MutationConfig:
    """One explicit cold/warm mutation applied to a ComfyUI workflow input."""

    node: str
    input: str
    cold: Scalar
    warm: Scalar
    path: str | None = None
    format: str = "value"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "node": self.node,
            "input": self.input,
            "cold": self.cold,
            "warm": self.warm,
            "format": self.format,
        }
        if self.path is not None:
            payload["path"] = self.path
        return payload


@dataclass(frozen=True)
class OverrideConfig:
    """One static workflow input override applied before a run or session."""

    node: str
    input: str
    value: Scalar
    path: str | None = None
    format: str = "value"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "node": self.node,
            "input": self.input,
            "value": self.value,
            "format": self.format,
        }
        if self.path is not None:
            payload["path"] = self.path
        return payload


@dataclass(frozen=True)
class SessionConfig:
    """Cold/warm generation contract for one benchmark session."""

    mutations: tuple[MutationConfig, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"mutations": [mutation.to_dict() for mutation in self.mutations]}


@dataclass(frozen=True)
class OutputConfig:
    """Select the benchmark artifact from a ComfyUI history output node."""

    node: str
    index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"node": self.node, "index": self.index}




@dataclass(frozen=True)
class WorkloadRequirements:
    """Runtime capabilities required by a workload before generation starts."""

    nodes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": list(self.nodes)}


@dataclass(frozen=True)
class RuntimeConfig:
    """Machine/runtime-specific ComfyUI launch configuration."""

    name: str
    comfyui_root: Path | None = None
    python: Path | None = None
    base_directory: Path | None = None
    extra_model_paths: tuple[Path, ...] = ()
    server_args: tuple[str, ...] = ()

@dataclass(frozen=True)
class WorkloadConfig:
    name: str
    workflow: Path
    overrides: tuple[OverrideConfig, ...] = ()
    session: SessionConfig | None = None
    output: OutputConfig | None = None
    requirements: WorkloadRequirements = WorkloadRequirements()

    def require_session(self) -> SessionConfig:
        if self.session is None:
            raise ValueError(
                "workload has no [[session.mutations]] contract; declare explicit cold/warm mutations"
            )
        return self.session

    def require_output(self) -> OutputConfig:
        if self.output is None:
            raise ValueError("workload has no [output] selector; declare output.node")
        return self.output

    def benchmark_contract(self) -> dict[str, Any]:
        contract: dict[str, Any] = {
            "session": self.require_session().to_dict(),
            "output": self.require_output().to_dict(),
        }
        if self.overrides:
            contract["overrides"] = [override.to_dict() for override in self.overrides]
        return contract


@dataclass(frozen=True)
class ComparisonConfig:
    name: str
    quality_metric: str = "ssim"
    min_ssim: float = 0.90


@dataclass(frozen=True)
class ComparisonContractConfig:
    name: str
    vary: tuple[ComparisonFactor, ...]
    min_ssim: float = 0.90


@dataclass(frozen=True)
class ProfileConfig:
    """Experiment variables applied on top of a runtime."""

    name: str
    server_args: tuple[str, ...] = ()


def _load_toml(path: Path) -> dict[str, Any]:
    parsed = tomllib.loads(path.read_text())
    if not isinstance(parsed, dict):
        raise ValueError(f"TOML config must contain a table: {path}")
    return parsed


def _required_string(data: dict[str, Any], key: str, *, source: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: {key} must be a non-empty string")
    return value


def _mutation_scalar(value: Any, *, field: str, source: Path) -> Scalar:
    if isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"{source}: session mutation {field} must be a TOML scalar")


def _load_override_value(value: Any, *, source: Path) -> Scalar:
    if isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"{source}: override value must be a TOML scalar")


def _load_path_format(
    item: dict[str, Any],
    *,
    source: Path,
    label: str,
) -> tuple[str | None, str]:
    path_value = item.get("path")
    if path_value is not None and (not isinstance(path_value, str) or not path_value.strip()):
        raise ValueError(f"{source}: {label} path must be a non-empty string")
    fmt = item.get("format", "value")
    if fmt not in {"value", "json"}:
        raise ValueError(f"{source}: unsupported {label} format: {fmt!r}")
    if fmt == "value" and path_value is not None:
        raise ValueError(f"{source}: {label} path requires format = \"json\"")
    if fmt == "json" and path_value is None:
        raise ValueError(f"{source}: format = \"json\" requires {label} path")
    return path_value, fmt


def _load_overrides(data: dict[str, Any], *, source: Path) -> tuple[OverrideConfig, ...]:
    raw = data.get("overrides", [])
    if not isinstance(raw, list):
        raise ValueError(f"{source}: [[overrides]] must be an array of tables")

    overrides: list[OverrideConfig] = []
    seen_targets: set[tuple[str, str, str | None]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{source}: overrides[{index}] must be a table")
        node = _required_string(item, "node", source=source)
        input_name = _required_string(item, "input", source=source)
        if "value" not in item:
            raise ValueError(f"{source}: override {node}.{input_name} requires value")
        value = _load_override_value(item["value"], source=source)
        path_value, fmt = _load_path_format(item, source=source, label="override")
        target = (node, input_name, path_value)
        if target in seen_targets:
            raise ValueError(f"{source}: duplicate override target: {target}")
        seen_targets.add(target)
        overrides.append(
            OverrideConfig(
                node=node,
                input=input_name,
                value=value,
                path=path_value,
                format=fmt,
            )
        )
    return tuple(overrides)


def _load_session(data: dict[str, Any], *, source: Path) -> SessionConfig | None:
    raw = data.get("session")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: session must be a table")

    raw_mutations = raw.get("mutations")
    if not isinstance(raw_mutations, list) or not raw_mutations:
        raise ValueError(f"{source}: [[session.mutations]] must contain at least one mutation")

    mutations: list[MutationConfig] = []
    seen_targets: set[tuple[str, str, str | None]] = set()
    for index, item in enumerate(raw_mutations):
        if not isinstance(item, dict):
            raise ValueError(f"{source}: session.mutations[{index}] must be a table")
        node = _required_string(item, "node", source=source)
        input_name = _required_string(item, "input", source=source)
        cold = _mutation_scalar(item.get("cold"), field="cold", source=source)
        warm = _mutation_scalar(item.get("warm"), field="warm", source=source)
        if cold == warm:
            raise ValueError(
                f"{source}: session mutation {node}.{input_name} cold and warm values must differ"
            )
        path_value, fmt = _load_path_format(item, source=source, label="mutation")
        target = (node, input_name, path_value)
        if target in seen_targets:
            raise ValueError(f"{source}: duplicate session mutation target: {target}")
        seen_targets.add(target)
        mutations.append(
            MutationConfig(
                node=node,
                input=input_name,
                cold=cold,
                warm=warm,
                path=path_value,
                format=fmt,
            )
        )
    return SessionConfig(mutations=tuple(mutations))


def _load_output(data: dict[str, Any], *, source: Path) -> OutputConfig | None:
    raw = data.get("output")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: output must be a table")
    node = _required_string(raw, "node", source=source)
    index = raw.get("index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError(f"{source}: output.index must be a non-negative integer")
    return OutputConfig(node=node, index=index)


def _load_requirements(data: dict[str, Any], *, source: Path) -> WorkloadRequirements:
    raw = data.get("requirements")
    if raw is None:
        return WorkloadRequirements()
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: requirements must be a table")
    nodes = raw.get("nodes", [])
    if not isinstance(nodes, list) or not all(isinstance(item, str) and item.strip() for item in nodes):
        raise ValueError(f"{source}: requirements.nodes must be an array of non-empty strings")
    normalized = tuple(nodes)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{source}: requirements.nodes must not contain duplicates")
    return WorkloadRequirements(nodes=normalized)


def load_runtime(path: Path) -> RuntimeConfig:
    data = _load_toml(path)
    name = _required_string(data, "name", source=path)

    comfyui_root_value = data.get("comfyui_root")
    if comfyui_root_value is not None and not isinstance(comfyui_root_value, str):
        raise ValueError(f"{path}: comfyui_root must be a string")
    python_value = data.get("python")
    if python_value is not None and not isinstance(python_value, str):
        raise ValueError(f"{path}: python must be a string")
    base_value = data.get("base_directory")
    if base_value is not None and not isinstance(base_value, str):
        raise ValueError(f"{path}: base_directory must be a string")
    extra_paths = data.get("extra_model_paths", [])
    if not isinstance(extra_paths, list) or not all(isinstance(item, str) for item in extra_paths):
        raise ValueError(f"{path}: extra_model_paths must be an array of strings")
    server_args = data.get("server_args", [])
    if not isinstance(server_args, list) or not all(isinstance(item, str) for item in server_args):
        raise ValueError(f"{path}: server_args must be an array of strings")

    return RuntimeConfig(
        name=name,
        comfyui_root=Path(comfyui_root_value).expanduser() if comfyui_root_value else None,
        python=Path(python_value) if python_value else None,
        base_directory=Path(base_value) if base_value else None,
        extra_model_paths=tuple(Path(item) for item in extra_paths),
        server_args=tuple(server_args),
    )


def load_workload(path: Path) -> WorkloadConfig:
    data = _load_toml(path)
    name = _required_string(data, "name", source=path)
    workflow_value = _required_string(data, "workflow", source=path)
    workflow = Path(workflow_value)
    if not workflow.is_absolute():
        workflow = path.parent / workflow
    if not workflow.is_file():
        raise FileNotFoundError(f"workflow does not exist: {workflow}")
    overrides = _load_overrides(data, source=path)
    session = _load_session(data, source=path)
    if session is not None:
        override_targets = {(item.node, item.input, item.path) for item in overrides}
        mutation_targets = {(item.node, item.input, item.path) for item in session.mutations}
        conflicts = sorted(override_targets & mutation_targets)
        if conflicts:
            raise ValueError(
                f"{path}: workflow target cannot be both override and session mutation: {conflicts[0]}"
            )
    return WorkloadConfig(
        name=name,
        workflow=workflow,
        overrides=overrides,
        session=session,
        output=_load_output(data, source=path),
        requirements=_load_requirements(data, source=path),
    )


def load_profile(path: Path) -> ProfileConfig:
    data = _load_toml(path)
    name = _required_string(data, "name", source=path)

    forbidden = sorted({"comfyui_root", "python", "base_directory", "extra_model_paths"}.intersection(data))
    if forbidden:
        raise ValueError(
            f"{path}: machine-specific fields belong in runtime config, not profile: {', '.join(forbidden)}"
        )

    server_args_value = data.get("server_args", [])
    if not isinstance(server_args_value, list) or not all(isinstance(item, str) for item in server_args_value):
        raise ValueError(f"{path}: server_args must be an array of strings")

    return ProfileConfig(name=name, server_args=tuple(server_args_value))

def load_comparison(path: Path) -> ComparisonConfig:
    data = _load_toml(path)
    name = _required_string(data, "name", source=path)
    quality = data.get("quality", {})
    if not isinstance(quality, dict):
        raise ValueError(f"{path}: quality must be a table")

    metric = quality.get("metric", "ssim")
    if metric != "ssim":
        raise ValueError(f"{path}: unsupported quality metric: {metric!r}")

    threshold = quality.get("min_ssim", 0.90)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError(f"{path}: quality.min_ssim must be a number")
    threshold = float(threshold)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"{path}: quality.min_ssim must be between 0 and 1")

    return ComparisonConfig(
        name=name,
        quality_metric=metric,
        min_ssim=threshold,
    )


def load_comparison_contract(path: Path) -> ComparisonContractConfig:
    data = _load_toml(path)
    name = _required_string(data, "name", source=path)
    raw_vary = data.get("vary")
    allowed = {"workload", "runtime", "profile"}
    if not isinstance(raw_vary, list) or not raw_vary:
        raise ValueError(f"{path}: vary must be a non-empty array")
    if not all(isinstance(item, str) and item in allowed for item in raw_vary):
        raise ValueError(
            f"{path}: vary may contain only workload, runtime, and profile"
        )
    if len(set(raw_vary)) != len(raw_vary):
        raise ValueError(f"{path}: vary must not contain duplicates")

    quality = data.get("quality", {})
    if not isinstance(quality, dict):
        raise ValueError(f"{path}: quality must be a table")
    metric = quality.get("metric", "ssim")
    if metric != "ssim":
        raise ValueError(f"{path}: unsupported quality metric: {metric!r}")
    threshold = quality.get("min_ssim", 0.90)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError(f"{path}: quality.min_ssim must be a number")
    threshold = float(threshold)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"{path}: quality.min_ssim must be between 0 and 1")

    return ComparisonContractConfig(
        name=name,
        vary=tuple(raw_vary),  # type: ignore[arg-type]
        min_ssim=threshold,
    )
