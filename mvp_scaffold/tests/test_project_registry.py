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
