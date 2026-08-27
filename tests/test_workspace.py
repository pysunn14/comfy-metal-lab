from __future__ import annotations

import json
from pathlib import Path

import pytest

from comfy_metal.workspace import import_workload, init_workspace


def _workflow(path: Path, *, ambiguous: bool = False) -> Path:
    payload = {
        "7": {"class_type": "KSampler", "inputs": {"seed": 123, "steps": 8}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
    }
    if ambiguous:
        payload["8"] = {"class_type": "KSampler", "inputs": {"seed": 456, "steps": 8}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_init_workspace_creates_managed_layout_and_safe_defaults(tmp_path: Path) -> None:
    root = tmp_path / ".comfy-metal"

    result = init_workspace(root)

    assert result.root == root
    for name in ("workloads", "runtimes", "profiles", "comparisons", "results", "logs", "docs"):
        assert (root / name).is_dir()
    assert (root / ".gitignore").read_text() == "*\n!.gitignore\n"
    assert (root / "runtimes" / "local.toml").read_text() == 'name = "local"\n'
    assert (root / "profiles" / "stock.toml").read_text() == 'name = "stock"\nserver_args = []\n'


def test_init_workspace_is_idempotent_and_does_not_overwrite_configs(tmp_path: Path) -> None:
    root = tmp_path / ".comfy-metal"
    init_workspace(root)
    runtime = root / "runtimes" / "local.toml"
    runtime.write_text('name = "custom-local"\n', encoding="utf-8")

    init_workspace(root)

    assert runtime.read_text() == 'name = "custom-local"\n'


def test_init_workspace_detects_adjacent_comfyui_for_default_runtime(tmp_path: Path) -> None:
    project = tmp_path / "comfy-metal-lab"
    project.mkdir()
    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    (comfyui / "main.py").write_text("# ComfyUI\n", encoding="utf-8")
    root = project / ".comfy-metal"

    init_workspace(root)

    runtime = (root / "runtimes" / "local.toml").read_text(encoding="utf-8")
    assert 'name = "local"' in runtime
    assert f'comfyui_root = "{comfyui.resolve()}"' in runtime


def test_init_workspace_backfills_only_untouched_default_runtime(tmp_path: Path) -> None:
    project = tmp_path / "comfy-metal-lab"
    project.mkdir()
    root = project / ".comfy-metal"
    init_workspace(root)
    runtime_path = root / "runtimes" / "local.toml"
    assert runtime_path.read_text(encoding="utf-8") == 'name = "local"\n'

    comfyui = tmp_path / "ComfyUI"
    comfyui.mkdir()
    (comfyui / "main.py").write_text("# ComfyUI\n", encoding="utf-8")
    init_workspace(root)

    assert f'comfyui_root = "{comfyui.resolve()}"' in runtime_path.read_text(encoding="utf-8")

    runtime_path.write_text('name = "local"\nserver_args = ["--disable-auto-launch"]\n', encoding="utf-8")
    init_workspace(root)
    assert runtime_path.read_text(encoding="utf-8") == (
        'name = "local"\nserver_args = ["--disable-auto-launch"]\n'
    )


def test_import_workload_copies_external_workflow_and_generates_manifest(tmp_path: Path) -> None:
    root = tmp_path / ".comfy-metal"
    init_workspace(root)
    source_dir = tmp_path / "Downloads" / "cats"
    source_dir.mkdir(parents=True)
    source = _workflow(source_dir / "workflow_api.json")

    result = import_workload(source, workspace_root=root, name="cat-test")

    target = root / "workloads" / "cat-test"
    assert result.directory == target
    assert json.loads((target / "workflow_api.json").read_text()) == json.loads(source.read_text())
    manifest = (target / "workload.toml").read_text()
    assert 'name = "cat-test"' in manifest
    assert 'workflow = "workflow_api.json"' in manifest
    assert 'input = "seed"' in manifest
    inspection = json.loads((target / "inspection.json").read_text())
    assert inspection["can_autogenerate"] is True


def test_import_workload_uses_parent_name_for_generic_workflow_filename(tmp_path: Path) -> None:
    root = tmp_path / ".comfy-metal"
    init_workspace(root)
    source_dir = tmp_path / "Downloads" / "My Cool Workflow"
    source_dir.mkdir(parents=True)
    source = _workflow(source_dir / "workflow_api.json")

    result = import_workload(source, workspace_root=root)

    assert result.name == "my-cool-workflow"
    assert result.directory.name == "my-cool-workflow"


def test_import_workload_refuses_ambiguous_workflow_without_partial_directory(tmp_path: Path) -> None:
    root = tmp_path / ".comfy-metal"
    init_workspace(root)
    source = _workflow(tmp_path / "ambiguous.json", ambiguous=True)

    with pytest.raises(ValueError, match="ambiguous"):
        import_workload(source, workspace_root=root, name="ambiguous")

    assert not (root / "workloads" / "ambiguous").exists()


def test_import_workload_never_overwrites_existing_workload(tmp_path: Path) -> None:
    root = tmp_path / ".comfy-metal"
    init_workspace(root)
    source = _workflow(tmp_path / "workflow.json")
    import_workload(source, workspace_root=root, name="demo")

    with pytest.raises(FileExistsError, match="already exists"):
        import_workload(source, workspace_root=root, name="demo")

from comfy_metal.workspace import allocate_result_dir, resolve_managed_config


def test_resolve_managed_config_supports_short_names_and_explicit_paths(tmp_path: Path) -> None:
    root = tmp_path / ".comfy-metal"
    init_workspace(root)
    workload_dir = root / "workloads" / "demo"
    workload_dir.mkdir()
    workload_file = workload_dir / "workload.toml"
    workload_file.write_text('name = "demo"\nworkflow = "workflow_api.json"\n')

    assert resolve_managed_config(Path("demo"), kind="workload", workspace_root=root) == workload_file
    assert resolve_managed_config(Path("local"), kind="runtime", workspace_root=root) == root / "runtimes" / "local.toml"
    explicit = tmp_path / "custom.toml"
    explicit.write_text('name = "custom"\n')
    assert resolve_managed_config(explicit, kind="profile", workspace_root=root) == explicit


def test_allocate_result_dir_uses_next_available_index(tmp_path: Path) -> None:
    root = tmp_path / ".comfy-metal"
    init_workspace(root)
    first = allocate_result_dir(root, workload_name="demo", profile_name="stock", prefix="bench")
    assert first == root / "results" / "demo" / "stock" / "bench-001"
    first.mkdir(parents=True)
    second = allocate_result_dir(root, workload_name="demo", profile_name="stock", prefix="bench")
    assert second == root / "results" / "demo" / "stock" / "bench-002"
