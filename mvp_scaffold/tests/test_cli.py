from pathlib import Path
import subprocess

from src import cli


def test_cli_dispatches_native_command(monkeypatch) -> None:
    captured: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(cli, "_run_native_command", lambda command, args: captured.append((command, args)) or 0)

    code = cli.main(["status"])

    assert code == 0
    assert captured == [("status", [])]


def test_cli_dispatches_native_check(monkeypatch) -> None:
    captured: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(cli, "_run_native_command", lambda command, args: captured.append((command, args)) or 0)

    code = cli.main(["check"])

    assert code == 0
    assert captured == [("check", [])]


def test_cli_forwards_script_command(monkeypatch) -> None:
    captured: list[list[str]] = []
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "install_start.sh"

    def fake_run(command, check=False):  # noqa: ANN001, FBT002
        _ = check
        captured.append(list(command))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(cli, "_script_path", lambda: script_path)
    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["update"])

    assert code == 0
    assert captured == [["bash", str(script_path), "update"]]


def test_cli_runs_docker_up_from_repo_root(monkeypatch) -> None:
    captured: list[tuple[list[str], str | None]] = []
    repo_root = Path(__file__).resolve().parents[2]

    def fake_run(command, check=False, cwd=None):  # noqa: ANN001, FBT002
        _ = check
        captured.append((list(command), cwd))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["docker-up"])

    assert code == 0
    assert captured == [(["docker", "compose", "up", "-d", "--build"], str(repo_root))]


def test_cli_init_home_bootstraps_runtime_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENFISH_HOME", str(tmp_path / "home"))

    code = cli.main(["init-home"])

    assert code == 0
    env_file = tmp_path / "home" / ".env"
    projects_file = tmp_path / "home" / "projects.yaml"
    assert env_file.exists()
    assert "PROJECTS_CONFIG_PATH=./projects.yaml" in env_file.read_text(encoding="utf-8")
    assert "MIGRATIONS_DIR=" in env_file.read_text(encoding="utf-8")
    assert projects_file.exists()
    assert "projects: {}" in projects_file.read_text(encoding="utf-8")


def test_cli_prefers_openfish_home_env_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENFISH_HOME", str(tmp_path / "home"))

    assert cli._env_file() == (tmp_path / "home" / ".env").resolve()


def test_cli_install_bootstraps_runtime_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENFISH_HOME", str(tmp_path / "home"))

    code = cli.main(["install"])

    assert code == 0
    assert (tmp_path / "home" / ".env").exists()
    assert (tmp_path / "home" / "projects.yaml").exists()


def test_cli_configure_writes_env_and_project(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENFISH_HOME", str(tmp_path / "home"))
    inputs = iter(
        [
            "123456789",
            "/tmp/projects",
            "/tmp/projects/demo",
            "demo",
            "Demo",
        ]
    )

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "token-123")
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    code = cli.main(["configure"])

    assert code == 0
    env_file = tmp_path / "home" / ".env"
    projects_file = tmp_path / "home" / "projects.yaml"
    assert "TELEGRAM_BOT_TOKEN=token-123" in env_file.read_text(encoding="utf-8")
    assert "ALLOWED_TELEGRAM_USER_IDS=123456789" in env_file.read_text(encoding="utf-8")
    assert "demo:" in projects_file.read_text(encoding="utf-8")


def test_cli_dispatches_native_tg_user_id_with_args(monkeypatch) -> None:
    captured: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(cli, "_run_native_command", lambda command, args: captured.append((command, args)) or 0)

    code = cli.main(["tg-user-id", "alice"])

    assert code == 0
    assert captured == [("tg-user-id", ["alice"])]
