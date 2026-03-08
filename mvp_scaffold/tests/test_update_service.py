from pathlib import Path
import subprocess

from src.update_service import UpdateService


class _Completed:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


def test_get_current_version_uses_git_metadata(monkeypatch) -> None:
    service = UpdateService(
        repo_root=Path("/tmp/repo"),
        script_path=Path("/tmp/repo/mvp_scaffold/scripts/install_start.sh"),
    )

    outputs = iter(
        [
            _Completed("main\n"),
            _Completed("v1.0.0-rc1\n"),
            _Completed("abc1234\n"),
        ]
    )

    monkeypatch.setattr(service, "_run", lambda command: next(outputs))

    version = service.get_current_version()

    assert version.branch == "main"
    assert version.version == "v1.0.0-rc1"
    assert version.commit == "abc1234"


def test_check_for_updates_fetches_and_collects_commits(monkeypatch) -> None:
    service = UpdateService(
        repo_root=Path("/tmp/repo"),
        script_path=Path("/tmp/repo/mvp_scaffold/scripts/install_start.sh"),
    )

    outputs = iter(
        [
            _Completed("main\n"),
            _Completed("v1.0.0-rc1\n"),
            _Completed("abc1234\n"),
            _Completed("origin/main\n"),
            _Completed(""),
            _Completed("def5678\n"),
            _Completed("2\n"),
            _Completed("0\n"),
            _Completed("def5678 fix: one\nfff9999 feat: two\n"),
        ]
    )

    monkeypatch.setattr(service, "_run", lambda command: next(outputs))

    result = service.check_for_updates()

    assert result.behind_count == 2
    assert result.ahead_count == 0
    assert result.commits == ["def5678 fix: one", "fff9999 feat: two"]
    assert result.upstream_ref == "origin/main"


def test_trigger_update_spawns_detached_script(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    script_path = repo_root / "mvp_scaffold/scripts/install_start.sh"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    service = UpdateService(repo_root=repo_root, script_path=script_path)
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):  # noqa: ANN001, ANN003
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = service.trigger_update()

    assert result.ok is True
    assert captured["command"] == [
        "bash",
        "-lc",
        f"sleep 2; exec bash {script_path} update",
    ]
    assert captured["kwargs"]["cwd"] == str(repo_root)
    assert captured["kwargs"]["start_new_session"] is True


def test_trigger_restart_spawns_detached_script(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    script_path = repo_root / "mvp_scaffold/scripts/install_start.sh"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    service = UpdateService(repo_root=repo_root, script_path=script_path)
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):  # noqa: ANN001, ANN003
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = service.trigger_restart()

    assert result.ok is True
    assert captured["command"] == [
        "bash",
        "-lc",
        f"sleep 2; exec bash {script_path} restart",
    ]
    assert captured["kwargs"]["cwd"] == str(repo_root)
    assert captured["kwargs"]["start_new_session"] is True


def test_read_logs_returns_recent_lines(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    log_dir = repo_root / "mvp_scaffold/data/logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "app.out.log").write_text("a\nb\nc\nd\n", encoding="utf-8")
    (log_dir / "update.log").write_text("u1\nu2\n", encoding="utf-8")
    service = UpdateService(
        repo_root=repo_root,
        script_path=repo_root / "mvp_scaffold/scripts/install_start.sh",
    )

    result = service.read_logs(app_lines=2, update_lines=1)

    assert result.ok is True
    assert "运行日志 (app.out.log):" in result.text
    assert "c\nd" in result.text
    assert "更新日志 (update.log):" in result.text
    assert "u2" in result.text


def test_clear_logs_truncates_files(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    log_dir = repo_root / "mvp_scaffold/data/logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    app_log = log_dir / "app.out.log"
    update_log = log_dir / "update.log"
    app_log.write_text("hello\n", encoding="utf-8")
    update_log.write_text("world\n", encoding="utf-8")
    service = UpdateService(
        repo_root=repo_root,
        script_path=repo_root / "mvp_scaffold/scripts/install_start.sh",
    )

    result = service.clear_logs()

    assert result.ok is True
    assert app_log.read_text(encoding="utf-8") == ""
    assert update_log.read_text(encoding="utf-8") == ""
