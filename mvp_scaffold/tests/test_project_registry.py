from pathlib import Path

from src.project_registry import ProjectRegistry


def test_project_registry_loads_memory_seed(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.yaml"
    config_path.write_text(
        """
projects:
  demo:
    path: ./demo
    allowed_directories:
      - ./demo
    memory_seed:
      summary: demo-summary
    notes:
      - keep changes small
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "demo").mkdir()

    registry = ProjectRegistry(config_path)
    registry.load()
    project = registry.get("demo")

    assert project is not None
    assert project.memory_seed_summary == "demo-summary"
    assert project.seed_notes == ["keep changes small"]


def test_project_registry_add_disable_archive(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    project_dir = tmp_path / "demo"
    project_dir.mkdir()

    registry = ProjectRegistry(config_path)
    registry.load()
    registry.add_project(key="demo", path=project_dir, name="Demo")

    assert registry.get("demo") is not None
    assert registry.list_keys() == ["demo"]

    disabled = registry.set_project_active(key="demo", is_active=False)
    assert disabled is True
    assert registry.get("demo") is None
    assert registry.list_keys() == []

    archived = registry.archive_project(key="demo")
    assert archived is True


def test_project_registry_default_root_load_and_set(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.yaml"
    root_path = tmp_path / "workspace"
    config_path.write_text(
        f"default_project_root: {root_path}\nprojects: {{}}\n",
        encoding="utf-8",
    )

    registry = ProjectRegistry(config_path)
    registry.load()
    assert registry.default_project_root == root_path.resolve()

    updated = registry.set_default_project_root(tmp_path / "workspace2")
    assert updated == (tmp_path / "workspace2").resolve()
    assert updated.exists()
    assert registry.default_project_root == updated


def test_project_registry_add_project_can_create_missing_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    target = tmp_path / "new_project"

    registry = ProjectRegistry(config_path)
    registry.load()
    registry.add_project(
        key="demo_new",
        path=target,
        name="Demo New",
        create_if_missing=True,
    )

    assert target.exists()
    assert target.is_dir()
    loaded = registry.get("demo_new")
    assert loaded is not None
    assert loaded.path == target.resolve()


def test_project_registry_loads_template_root_and_project_metadata(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.yaml"
    template_root = tmp_path / "templates"
    project_dir = tmp_path / "demo"
    template_root.mkdir()
    project_dir.mkdir()
    config_path.write_text(
        f"""
default_project_root: {tmp_path / "workspace"}
project_template_root: {template_root}
projects:
  demo:
    path: {project_dir}
    allowed_directories:
      - {project_dir}
    template_name: recon
    default_run_mode: autopilot
    default_autopilot_goal: 收集信息
""".strip(),
        encoding="utf-8",
    )

    registry = ProjectRegistry(config_path)
    registry.load()
    project = registry.get("demo")

    assert project is not None
    assert registry.project_template_root == template_root.resolve()
    assert project.template_name == "recon"
    assert project.default_run_mode == "autopilot"
    assert project.default_autopilot_goal == "收集信息"


def test_project_registry_lists_and_applies_templates(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    template_root = tmp_path / "templates"
    preset_dir = template_root / "recon"
    nested_dir = preset_dir / "notes"
    nested_dir.mkdir(parents=True)
    (preset_dir / ".openfish-template.yaml").write_text(
        """
name: 自动化信息收集
description: 收集域名、子域名和 URL
default_autopilot_goal: 对目标进行自动化信息收集
""".strip(),
        encoding="utf-8",
    )
    (preset_dir / "README.md").write_text("preset", encoding="utf-8")
    (nested_dir / "targets.txt").write_text("example.com", encoding="utf-8")

    registry = ProjectRegistry(config_path)
    registry.load()
    updated = registry.set_project_template_root(template_root)
    assert updated == template_root.resolve()

    presets = registry.list_project_templates()
    assert [preset.key for preset in presets] == ["recon"]
    assert presets[0].default_autopilot_goal == "对目标进行自动化信息收集"

    target = tmp_path / "workspace" / "demo"
    applied = registry.apply_project_template(template_key="recon", target_path=target)

    assert applied.key == "recon"
    assert (target / "README.md").read_text(encoding="utf-8") == "preset"
    assert (target / "notes" / "targets.txt").read_text(encoding="utf-8") == "example.com"
    assert not (target / ".openfish-template.yaml").exists()
