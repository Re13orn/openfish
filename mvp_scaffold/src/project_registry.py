"""Project registry loading and path validation from YAML."""

from pathlib import Path
import yaml

from src.models import ProjectConfig


class ProjectRegistry:
    """Loads registered projects and enforces directory boundaries."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.projects: dict[str, ProjectConfig] = {}

    def load(self) -> None:
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        raw_projects = data.get("projects", {})
        self.projects = {}

        for key, value in raw_projects.items():
            project_path = self._resolve_project_path(value["path"])
            allowed_raw = value.get("allowed_directories", [str(project_path)])
            allowed_directories = [self._resolve_project_path(raw) for raw in allowed_raw]
            memory_seed = value.get("memory_seed") or {}
            summary_seed = memory_seed.get("summary")
            notes_seed = value.get("notes") or []

            self.projects[key] = ProjectConfig(
                key=key,
                name=value.get("name", key),
                path=project_path,
                default_branch=value.get("default_branch"),
                test_command=value.get("test_command"),
                dev_command=value.get("dev_command"),
                description=value.get("description"),
                allowed_directories=allowed_directories,
                memory_seed_summary=str(summary_seed).strip() if summary_seed else None,
                seed_notes=[str(item).strip() for item in notes_seed if str(item).strip()],
            )

    def get(self, key: str) -> ProjectConfig | None:
        return self.projects.get(key)

    def list_keys(self) -> list[str]:
        return sorted(self.projects.keys())

    def is_path_allowed(self, project: ProjectConfig, candidate_path: Path) -> bool:
        """Check whether a target path stays within the project's allowed directories."""

        resolved_candidate = candidate_path.expanduser().resolve()
        for allowed in project.allowed_directories or []:
            try:
                resolved_candidate.relative_to(allowed)
                return True
            except ValueError:
                continue
        return False

    def _resolve_project_path(self, raw_path: str) -> Path:
        base_path = Path(raw_path).expanduser()
        if base_path.is_absolute():
            return base_path.resolve()
        return (self.config_path.parent / base_path).resolve()
