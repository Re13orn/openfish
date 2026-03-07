"""Configuration loading from environment variables."""

from dataclasses import dataclass
import os
from pathlib import Path


def _split_csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _split_csv_list(value: str) -> tuple[str, ...]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    return tuple(dict.fromkeys(items))


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_runtime_path(raw: str, *, repo_root: Path, app_root: Path) -> Path:
    candidate = Path(os.path.expanduser(raw))
    if candidate.is_absolute():
        return candidate

    normalized = raw.strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("mvp_scaffold/"):
        return repo_root / normalized
    return app_root / normalized


@dataclass(slots=True)
class AppConfig:
    """Runtime settings for the local single-process assistant."""

    telegram_bot_token: str
    allowed_telegram_user_ids: set[str]
    projects_config_path: Path
    default_project_root: Path | None
    sqlite_path: Path
    schema_path: Path
    migrations_dir: Path
    process_lock_path: Path
    log_level: str
    codex_bin: str
    codex_json_output: bool
    codex_default_sandbox_mode: str
    codex_default_approval_mode: str
    codex_command_timeout_seconds: int
    codex_home: Path
    codex_model_choices: tuple[str, ...]
    enable_skill_install: bool
    skill_install_timeout_seconds: int
    poll_interval_seconds: int
    max_telegram_message_length: int
    telegram_reconnect_initial_delay_seconds: float
    telegram_reconnect_max_delay_seconds: float
    telegram_reconnect_jitter_seconds: float
    enable_scheduler: bool
    schedule_poll_interval_seconds: int
    schedule_missed_run_policy: str
    enable_document_upload: bool
    max_upload_size_bytes: int
    upload_temp_dir_name: str
    allowed_upload_extensions: set[str]


def load_config() -> AppConfig:
    """Load and validate process configuration from environment variables."""

    allowed_user_ids = _split_csv(os.environ["ALLOWED_TELEGRAM_USER_IDS"])
    if not allowed_user_ids:
        raise ValueError("ALLOWED_TELEGRAM_USER_IDS must contain at least one user id.")

    repo_root = Path(__file__).resolve().parents[2]
    app_root = Path(__file__).resolve().parents[1]
    allowed_upload_extensions = _split_csv(
        os.getenv(
            "ALLOWED_UPLOAD_EXTENSIONS",
            "txt,md,markdown,json,yaml,yml,xml,csv,log,ini,toml,py,js,ts,tsx,jsx,go,rs,java,kt,swift,sql,html,css,apk,zip",
        )
    )
    normalized_upload_extensions = {ext.lower() for ext in allowed_upload_extensions}
    normalized_upload_extensions.add("zip")
    schedule_missed_run_policy = os.getenv("SCHEDULE_MISSED_RUN_POLICY", "skip").strip().lower()
    if schedule_missed_run_policy not in {"skip", "catchup_once"}:
        schedule_missed_run_policy = "skip"

    sqlite_path = _resolve_runtime_path(
        os.getenv("SQLITE_PATH", "./data/app.db"),
        repo_root=repo_root,
        app_root=app_root,
    )
    process_lock_path = _resolve_runtime_path(
        os.getenv("OPENFISH_LOCK_PATH", str(sqlite_path.parent / "openfish.lock")),
        repo_root=repo_root,
        app_root=app_root,
    )

    return AppConfig(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        allowed_telegram_user_ids=allowed_user_ids,
        projects_config_path=_resolve_runtime_path(
            os.getenv("PROJECTS_CONFIG_PATH", "./projects.yaml"),
            repo_root=repo_root,
            app_root=app_root,
        ),
        default_project_root=(
            Path(os.path.expanduser(os.getenv("DEFAULT_PROJECT_ROOT", ""))).resolve()
            if os.getenv("DEFAULT_PROJECT_ROOT", "").strip()
            else None
        ),
        sqlite_path=sqlite_path,
        schema_path=_resolve_runtime_path(
            os.getenv("SCHEMA_PATH", str(repo_root / "schema.sql")),
            repo_root=repo_root,
            app_root=app_root,
        ),
        migrations_dir=_resolve_runtime_path(
            os.getenv("MIGRATIONS_DIR", str(app_root / "migrations")),
            repo_root=repo_root,
            app_root=app_root,
        ),
        process_lock_path=process_lock_path,
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        codex_bin=os.getenv("CODEX_BIN", "codex"),
        codex_json_output=_parse_bool(os.getenv("CODEX_JSON_OUTPUT"), default=True),
        codex_default_sandbox_mode=os.getenv("CODEX_DEFAULT_SANDBOX_MODE", "workspace-write"),
        codex_default_approval_mode=os.getenv("CODEX_DEFAULT_APPROVAL_MODE", "never"),
        codex_command_timeout_seconds=int(os.getenv("CODEX_COMMAND_TIMEOUT_SECONDS", "1800")),
        codex_home=Path(os.path.expanduser(os.getenv("CODEX_HOME", "~/.codex"))).resolve(),
        codex_model_choices=_split_csv_list(
            os.getenv("CODEX_MODEL_CHOICES", "gpt-5.4,gpt-5,o3")
        ),
        enable_skill_install=_parse_bool(os.getenv("ENABLE_SKILL_INSTALL"), default=True),
        skill_install_timeout_seconds=int(os.getenv("SKILL_INSTALL_TIMEOUT_SECONDS", "600")),
        poll_interval_seconds=int(os.getenv("TELEGRAM_POLL_INTERVAL_SECONDS", "2")),
        max_telegram_message_length=int(os.getenv("MAX_TELEGRAM_MESSAGE_LENGTH", "3500")),
        telegram_reconnect_initial_delay_seconds=float(
            os.getenv("TELEGRAM_RECONNECT_INITIAL_DELAY_SECONDS", "2")
        ),
        telegram_reconnect_max_delay_seconds=float(
            os.getenv("TELEGRAM_RECONNECT_MAX_DELAY_SECONDS", "300")
        ),
        telegram_reconnect_jitter_seconds=float(
            os.getenv("TELEGRAM_RECONNECT_JITTER_SECONDS", "1")
        ),
        enable_scheduler=_parse_bool(os.getenv("ENABLE_SCHEDULER"), default=True),
        schedule_poll_interval_seconds=int(os.getenv("SCHEDULE_POLL_INTERVAL_SECONDS", "20")),
        schedule_missed_run_policy=schedule_missed_run_policy,
        enable_document_upload=_parse_bool(os.getenv("ENABLE_DOCUMENT_UPLOAD"), default=True),
        max_upload_size_bytes=int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(200 * 1024 * 1024))),
        upload_temp_dir_name=os.getenv("UPLOAD_TEMP_DIR_NAME", ".codex_telegram_uploads"),
        allowed_upload_extensions=normalized_upload_extensions,
    )
