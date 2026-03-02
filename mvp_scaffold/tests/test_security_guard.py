from pathlib import Path

from src.security_guard import has_symlink_in_path, is_sensitive_file_name


def test_sensitive_file_name_detection() -> None:
    assert is_sensitive_file_name(".env") is True
    assert is_sensitive_file_name("id_rsa.txt") is True
    assert is_sensitive_file_name("report.apk") is False


def test_has_symlink_in_path_without_symlink(tmp_path: Path) -> None:
    project = tmp_path / "project"
    target = project / ".codex_telegram_uploads" / "a.txt"
    target.parent.mkdir(parents=True)
    assert has_symlink_in_path(project, target) is False

