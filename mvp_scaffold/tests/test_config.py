from pathlib import Path

from src.config import load_config


def test_load_config_always_includes_zip_in_allowed_upload_extensions(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("ALLOWED_UPLOAD_EXTENSIONS", "txt,md,json")

    config = load_config()

    assert "zip" in config.allowed_upload_extensions
    assert "txt" in config.allowed_upload_extensions
    assert "json" in config.allowed_upload_extensions
    assert config.process_lock_path.name == "openfish.lock"
    assert config.codex_model_choices == ("gpt-5.4", "gpt-5", "o3")
    assert config.telegram_connection_pool_size == 64
    assert config.telegram_send_local_file_max_size_bytes == 49 * 1024 * 1024
    assert config.telegram_pool_timeout_seconds == 15.0
    assert config.telegram_get_updates_connection_pool_size == 8
    assert config.telegram_get_updates_pool_timeout_seconds == 30.0
    assert config.codex_background_terminal_wait_timeout_seconds == 120

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ALLOWED_TELEGRAM_USER_IDS", raising=False)
    monkeypatch.delenv("ALLOWED_UPLOAD_EXTENSIONS", raising=False)


def test_load_config_resolves_repo_relative_runtime_paths(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("PROJECTS_CONFIG_PATH", "./mvp_scaffold/projects.yaml")
    monkeypatch.setenv("SQLITE_PATH", "./mvp_scaffold/data/app.db")
    monkeypatch.setenv("MIGRATIONS_DIR", "./mvp_scaffold/migrations")
    monkeypatch.setenv("OPENFISH_LOCK_PATH", "./mvp_scaffold/data/openfish.lock")

    config = load_config()

    repo_root = Path(__file__).resolve().parents[2]
    assert config.projects_config_path == repo_root / "mvp_scaffold/projects.yaml"
    assert config.sqlite_path == repo_root / "mvp_scaffold/data/app.db"
    assert config.migrations_dir == repo_root / "mvp_scaffold/migrations"
    assert config.process_lock_path == repo_root / "mvp_scaffold/data/openfish.lock"


def test_load_config_defaults_ui_mode_to_stream(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")

    config = load_config()

    assert config.default_ui_mode == "stream"


def test_load_config_invalid_ui_mode_falls_back_to_stream(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("DEFAULT_UI_MODE", "compact")

    config = load_config()

    assert config.default_ui_mode == "stream"
