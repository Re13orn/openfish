"""Application composition root."""

import logging
from pathlib import Path

from src.audit import AuditLogger
from src.approval import ApprovalService
from src.codex_runner import CodexRunner
from src.config import AppConfig, load_config
from src.db import Database
from src.project_registry import ProjectRegistry
from src.repo_inspector import RepoInspector
from src.router import CommandRouter
from src.skills_service import SkillsService
from src.task_store import TaskStore
from src.telegram_adapter import TelegramBotService


logger = logging.getLogger(__name__)


class Application:
    """Build and run the single-process assistant service."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db = Database(config.sqlite_path, config.schema_path, config.migrations_dir)
        self.projects = ProjectRegistry(config.projects_config_path)
        self.tasks = TaskStore(self.db)
        self.audit = AuditLogger(self.db)
        self.approvals = ApprovalService()
        self.codex = CodexRunner(config)
        self.skills = SkillsService(
            codex_bin=config.codex_bin,
            skills_root=config.codex_home / "skills",
            enable_install=config.enable_skill_install,
            timeout_seconds=config.skill_install_timeout_seconds,
        )
        self.repo = RepoInspector()
        self.router = CommandRouter(
            config=config,
            projects=self.projects,
            tasks=self.tasks,
            audit=self.audit,
            codex=self.codex,
            repo=self.repo,
            approvals=self.approvals,
            skills_service=self.skills,
        )
        self.bot = TelegramBotService(config=config, router=self.router)

    def run(self) -> None:
        self._configure_logging()
        self.projects.load()
        self._run_startup_health_checks()
        self.db.connect()
        self.db.initialize_schema()
        self.tasks.sync_projects_from_registry(self.projects)
        self.bot.run_polling()

    def _configure_logging(self) -> None:
        logging.basicConfig(
            level=getattr(logging, self.config.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    def _run_startup_health_checks(self) -> None:
        if not self.projects.projects:
            raise ValueError("Project registry is empty. Add at least one project in projects.yaml.")

        for key, project in self.projects.projects.items():
            if not self.projects.is_path_allowed(project, project.path):
                raise ValueError(
                    f"Project '{key}' path is not in allowed_directories: {project.path}"
                )
            project_path = Path(project.path)
            if not project_path.exists():
                logger.warning("Project path does not exist for key=%s path=%s", key, project_path)


def create_app() -> Application:
    """Factory used by CLI entry points and tests."""

    config = load_config()
    return Application(config)
