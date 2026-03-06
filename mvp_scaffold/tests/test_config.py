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

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ALLOWED_TELEGRAM_USER_IDS", raising=False)
    monkeypatch.delenv("ALLOWED_UPLOAD_EXTENSIONS", raising=False)
