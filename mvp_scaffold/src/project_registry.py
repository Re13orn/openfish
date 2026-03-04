"""Project registry loading and path validation from YAML."""

from datetime import datetime
from pathlib import Path
import re
import yaml

from src.models import ProjectConfig


PROJECT_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


class ProjectRegistry:
    """Loads registered projects and enforces directory boundaries."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.projects: dict[str, ProjectConfig] = {}
        self.default_project_root: Path | None = None

    def load(self) -> None:
        data = self._read_config()
        raw_projects = data.get("projects", {})
        self.projects = {}
        self.default_project_root = None
        default_root_raw = data.get("default_project_root")
        if isinstance(default_root_raw, str) and default_root_raw.strip():
            self.default_project_root = self._resolve_project_path(default_root_raw.strip())

        for key, value in raw_projects.items():
            if not isinstance(value, dict):
                continue
            project_path = self._resolve_project_path(value["path"])
            allowed_raw = value.get("allowed_directories", [str(project_path)])
            allowed_directories = [self._resolve_project_path(raw) for raw in allowed_raw]
            memory_seed = value.get("memory_seed") or {}
            summary_seed = memory_seed.get("summary")
            notes_seed = value.get("notes") or []
            is_active = bool(value.get("is_active", True))

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
                is_active=is_active,
            )

    def get(self, key: str) -> ProjectConfig | None:
        project = self.projects.get(key)
        if project is None:
            return None
        if not project.is_active:
            return None
        return project

    def get_any(self, key: str) -> ProjectConfig | None:
        return self.projects.get(key)

    def list_keys(self, *, include_inactive: bool = False) -> list[str]:
        keys = []
        for key, project in self.projects.items():
            if include_inactive or project.is_active:
                keys.append(key)
        return sorted(keys)

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

    def add_project(
        self,
        *,
        key: str,
        path: Path,
        name: str | None = None,
        create_if_missing: bool = False,
    ) -> None:
        normalized_key = key.strip()
        if not PROJECT_KEY_PATTERN.match(normalized_key):
            raise ValueError("项目 key 非法，只允许字母数字/._-，长度 1-64。")
        normalized_path = path.expanduser()
        if not normalized_path.is_absolute():
            raise ValueError("项目路径必须是绝对路径。")
        resolved_path = normalized_path.resolve()
        if create_if_missing:
            try:
                resolved_path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ValueError(f"无法创建项目目录: {resolved_path} ({exc})") from exc
        if not resolved_path.exists() or not resolved_path.is_dir():
            raise ValueError(f"项目路径不存在或不是目录: {resolved_path}")

        data = self._read_config()
        projects = data.setdefault("projects", {})
        if normalized_key in projects:
            raise ValueError(f"项目已存在: {normalized_key}")

        projects[normalized_key] = {
            "name": name or normalized_key,
            "path": str(resolved_path),
            "allowed_directories": [str(resolved_path)],
            "is_active": True,
        }
        self._write_config(data)
        self.load()

    def set_default_project_root(self, root_path: Path) -> Path:
        normalized = root_path.expanduser()
        if not normalized.is_absolute():
            raise ValueError("默认项目根目录必须是绝对路径。")
        resolved = normalized.resolve()
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"无法创建默认项目根目录: {resolved} ({exc})") from exc

        data = self._read_config()
        data["default_project_root"] = str(resolved)
        self._write_config(data)
        self.load()
        return resolved

    def set_project_active(self, *, key: str, is_active: bool) -> bool:
        data = self._read_config()
        projects = data.get("projects", {})
        if key not in projects:
            return False
        project_value = projects[key]
        if not isinstance(project_value, dict):
            project_value = {"path": str(self._resolve_project_path(str(project_value)))}
            projects[key] = project_value
        project_value["is_active"] = bool(is_active)
        self._write_config(data)
        self.load()
        return True

    def archive_project(self, *, key: str) -> bool:
        data = self._read_config()
        projects = data.get("projects", {})
        if key not in projects:
            return False
        project_value = projects[key]
        if not isinstance(project_value, dict):
            return False
        project_value["is_active"] = False
        project_value["archived_at"] = datetime.now().isoformat(timespec="seconds")
        self._write_config(data)
        self.load()
        return True

    def _read_config(self) -> dict:
        if not self.config_path.exists():
            return {}
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("projects config 必须是 YAML 对象。")
        return data

    def _write_config(self, data: dict) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        content = yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=False,
        )
        self.config_path.write_text(content, encoding="utf-8")
