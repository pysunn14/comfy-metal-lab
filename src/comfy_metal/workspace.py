"""Managed local workspace helpers for Comfy Metal Lab."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .inspection import inspect_workflow, render_workload_toml

WORKSPACE_DIRECTORIES = (
    "workloads",
    "runtimes",
    "profiles",
    "comparisons",
    "results",
    "logs",
    "docs",
)


@dataclass(frozen=True)
class WorkspaceInitResult:
    root: Path
    created: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"workspace": str(self.root), "created": list(self.created)}


@dataclass(frozen=True)
class WorkloadImportResult:
    name: str
    directory: Path
    workflow: Path
    manifest: Path
    inspection: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "directory": str(self.directory),
            "workflow": str(self.workflow),
            "manifest": str(self.manifest),
            "inspection": str(self.inspection),
        }


def _write_if_missing(path: Path, text: str, *, created: list[str], root: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    created.append(str(path.relative_to(root)))


def init_workspace(root: Path = Path(".comfy-metal")) -> WorkspaceInitResult:
    """Create an idempotent project-local managed workspace."""

    root = root.expanduser()
    created: list[str] = []
    if not root.exists():
        root.mkdir(parents=True)
        created.append(".")
    elif not root.is_dir():
        raise ValueError(f"workspace path is not a directory: {root}")

    for name in WORKSPACE_DIRECTORIES:
        directory = root / name
        if not directory.exists():
            directory.mkdir(parents=True)
            created.append(f"{name}/")
        elif not directory.is_dir():
            raise ValueError(f"workspace entry is not a directory: {directory}")

    # Protect local data even when the containing repository does not ignore .comfy-metal/.
    _write_if_missing(root / ".gitignore", "*\n!.gitignore\n", created=created, root=root)
    _write_if_missing(
        root / "runtimes" / "local.toml",
        'name = "local"\n',
        created=created,
        root=root,
    )
    _write_if_missing(
        root / "profiles" / "stock.toml",
        'name = "stock"\nserver_args = []\n',
        created=created,
        root=root,
    )
    return WorkspaceInitResult(root=root, created=tuple(created))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("workload name must contain at least one letter or digit")
    return slug


def _default_workload_name(source: Path) -> str:
    stem = source.stem.lower()
    if stem in {"workflow", "workflow-api", "workflow_api", "api"}:
        candidate = source.parent.name
    else:
        candidate = source.stem
    return _slugify(candidate)


def _require_workspace(root: Path) -> Path:
    root = root.expanduser()
    if not root.is_dir() or not (root / "workloads").is_dir():
        raise FileNotFoundError(
            f"managed workspace is not initialized: {root}; run `comfy-metal init` first"
        )
    return root


def import_workload(
    source: Path,
    *,
    workspace_root: Path = Path(".comfy-metal"),
    name: str | None = None,
) -> WorkloadImportResult:
    """Import one external API workflow into the managed workspace."""

    root = _require_workspace(workspace_root)
    source = source.expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"workflow does not exist: {source}")

    inspection = inspect_workflow(source)
    workload_name = _slugify(name) if name is not None else _default_workload_name(source)
    manifest_text = render_workload_toml(
        name=workload_name,
        workflow_name="workflow_api.json",
        inspection=inspection,
    )

    target = root / "workloads" / workload_name
    if target.exists():
        raise FileExistsError(f"managed workload already exists: {target}")

    target.mkdir(parents=True)
    workflow_path = target / "workflow_api.json"
    manifest_path = target / "workload.toml"
    inspection_path = target / "inspection.json"
    shutil.copy2(source, workflow_path)
    manifest_path.write_text(manifest_text, encoding="utf-8")
    inspection_path.write_text(
        json.dumps(inspection.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return WorkloadImportResult(
        name=workload_name,
        directory=target,
        workflow=workflow_path,
        manifest=manifest_path,
        inspection=inspection_path,
    )


def resolve_managed_config(
    value: Path,
    *,
    kind: str,
    workspace_root: Path = Path(".comfy-metal"),
) -> Path:
    """Resolve an existing explicit path or a short managed config name."""

    value = value.expanduser()
    if value.exists():
        return value
    if value.parent != Path("."):
        return value

    root = workspace_root.expanduser()
    if kind == "workload":
        candidate = root / "workloads" / value.name / "workload.toml"
    elif kind in {"runtime", "profile", "comparison"}:
        candidate = root / f"{kind}s" / f"{value.name}.toml"
    else:
        raise ValueError(f"unsupported managed config kind: {kind}")
    return candidate if candidate.exists() else value


def allocate_result_dir(
    workspace_root: Path,
    *,
    workload_name: str,
    profile_name: str,
    prefix: str,
) -> Path:
    """Return the next non-existing managed result directory."""

    root = _require_workspace(workspace_root)
    base = root / "results" / _slugify(workload_name) / _slugify(profile_name)
    for index in range(1, 10000):
        candidate = base / f"{prefix}-{index:03d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not allocate result directory under {base}")
