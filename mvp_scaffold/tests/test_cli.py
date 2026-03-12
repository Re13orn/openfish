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
    captured: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(cli, "_run_native_command", lambda command, args: captured.append((command, args)) or 0)

    code = cli.main(["update"])

    assert code == 0
    assert captured == [("update", [])]


def test_cli_runs_docker_up_from_repo_root(monkeypatch, tmp_path) -> None:
    captured: list[tuple[list[str], str | None]] = []
    repo_root = tmp_path
    env_file = repo_root / ".openfish.docker.env"
    (repo_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    env_file.write_text("TELEGRAM_BOT_TOKEN=token\n", encoding="utf-8")

    def fake_run(command, check=False, cwd=None):  # noqa: ANN001, FBT002
        _ = check
        captured.append((list(command), cwd))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli, "_docker_env_file", lambda: env_file)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli, "_validate_telegram_token", lambda token: (True, ""))
    monkeypatch.setattr(cli, "_docker_wait_for_running_state", lambda docker_bin, timeout_seconds=8.0: "running")
    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["docker-up"])

    assert code == 0
    assert captured == [(["/usr/bin/docker", "compose", "--env-file", str(env_file), "up", "-d", "--build"], str(repo_root))]


def test_cli_runs_docker_login_codex(monkeypatch) -> None:
    captured: list[list[str]] = []
    repo_root = Path(__file__).resolve().parents[2]

    def fake_run(command, check=False, cwd=None):  # noqa: ANN001, FBT002
        _ = check
        _ = cwd
        captured.append(list(command))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(cli, "_docker_require_running", lambda docker_bin, command_name: True)
    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["docker-login-codex"])

    assert code == 0
    assert captured == [["/usr/bin/docker", "exec", "openfish", "codex", "login", "--device-auth"]]


def test_cli_docker_up_requires_docker_config(monkeypatch, capsys) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_file = repo_root / ".openfish.docker.env"

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli, "_docker_env_file", lambda: env_file)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")

    code = cli.main(["docker-up"])

    assert code == 1
    err = capsys.readouterr().err
    assert "openfish docker-configure" in err


def test_cli_docker_up_rejects_invalid_token(monkeypatch, tmp_path, capsys) -> None:
    repo_root = tmp_path
    env_file = repo_root / ".openfish.docker.env"
    (repo_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    env_file.write_text("TELEGRAM_BOT_TOKEN=bad-token\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli, "_docker_env_file", lambda: env_file)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli, "_validate_telegram_token", lambda token: (False, "Unauthorized"))

    code = cli.main(["docker-up"])

    assert code == 1
    err = capsys.readouterr().err
    assert "Telegram Bot Token 校验失败" in err


def test_cli_docker_configure_writes_env_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".openfish.docker.env"
    inputs = iter(["123456789", "demo", "demo", "Demo"])

    monkeypatch.setattr(cli, "_docker_env_file", lambda: env_file)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "token-123")
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(cli, "_suggest_telegram_user_ids", lambda token: None)
    monkeypatch.setattr(cli, "_validate_telegram_token", lambda token: (True, ""))

    code = cli.main(["docker-configure"])

    assert code == 0
    content = env_file.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=token-123" in content
    assert "ALLOWED_TELEGRAM_USER_IDS=123456789" in content
    assert "DEFAULT_PROJECT_ROOT=/workspace/projects" in content
    assert "OPENFISH_BOOTSTRAP_PROJECT_KEY=demo" in content


def test_cli_docker_configure_allows_empty_optional_fields(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".openfish.docker.env"
    inputs = iter(["123456789", "", "", ""])

    monkeypatch.setattr(cli, "_docker_env_file", lambda: env_file)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "token-123")
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(cli, "_suggest_telegram_user_ids", lambda token: None)
    monkeypatch.setattr(cli, "_validate_telegram_token", lambda token: (True, ""))

    code = cli.main(["docker-configure"])

    assert code == 0
    content = env_file.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=token-123" in content
    assert "ALLOWED_TELEGRAM_USER_IDS=123456789" in content
    assert "DEFAULT_PROJECT_ROOT=/workspace/projects" in content
    assert "DEFAULT_PROJECT=" not in content
    assert "OPENFISH_BOOTSTRAP_PROJECT_KEY=" not in content
    assert "OPENFISH_BOOTSTRAP_PROJECT_NAME=" not in content


def test_cli_docker_configure_rejects_invalid_token(monkeypatch, tmp_path, capsys) -> None:
    env_file = tmp_path / ".openfish.docker.env"
    inputs = iter(["123456789"])

    monkeypatch.setattr(cli, "_docker_env_file", lambda: env_file)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "bad-token")
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(cli, "_suggest_telegram_user_ids", lambda token: None)
    monkeypatch.setattr(cli, "_validate_telegram_token", lambda token: (False, "Unauthorized"))

    code = cli.main(["docker-configure"])

    assert code == 1
    err = capsys.readouterr().err
    assert "Telegram Bot Token 校验失败" in err
    assert not env_file.exists()


def test_cli_docker_login_codex_imports_auth_file(monkeypatch, tmp_path) -> None:
    captured: list[tuple[list[str], str | None]] = []
    repo_root = Path(__file__).resolve().parents[2]
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"access_token":"abc"}', encoding="utf-8")

    def fake_run(command, check=False, cwd=None, input=None, text=None):  # noqa: ANN001, FBT002
        _ = check
        _ = text
        captured.append((list(command), input))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli, "_docker_require_running", lambda docker_bin, command_name: True)
    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["docker-login-codex", str(auth_file)])

    assert code == 0
    assert captured[0][0] == [
        "/usr/bin/docker",
        "exec",
        "-i",
        "openfish",
        "/bin/sh",
        "-lc",
        "mkdir -p /root/.codex && cat > /root/.codex/auth.json && chmod 600 /root/.codex/auth.json",
    ]
    assert captured[0][1] == '{"access_token":"abc"}'
    assert captured[1][0] == ["/usr/bin/docker", "exec", "openfish", "codex", "login", "status"]


def test_cli_docker_login_codex_pastes_auth_content(monkeypatch) -> None:
    captured: list[tuple[list[str], str | None]] = []
    repo_root = Path(__file__).resolve().parents[2]
    answers = iter(["paste", '{"access_token":"xyz"}'])

    def fake_run(command, check=False, cwd=None, input=None, text=None):  # noqa: ANN001, FBT002
        _ = check
        _ = cwd
        _ = text
        captured.append((list(command), input))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(cli, "_docker_require_running", lambda docker_bin, command_name: True)
    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["docker-login-codex"])

    assert code == 0
    assert captured[0][1] == '{"access_token":"xyz"}'


def test_cli_docker_login_codex_accepts_numeric_path_choice(monkeypatch, tmp_path) -> None:
    captured: list[tuple[list[str], str | None]] = []
    repo_root = Path(__file__).resolve().parents[2]
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"access_token":"num"}', encoding="utf-8")
    answers = iter(["2", str(auth_file)])

    def fake_run(command, check=False, cwd=None, input=None, text=None):  # noqa: ANN001, FBT002
        _ = check
        _ = cwd
        _ = text
        captured.append((list(command), input))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(cli, "_docker_require_running", lambda docker_bin, command_name: True)
    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["docker-login-codex"])

    assert code == 0
    assert captured[0][1] == '{"access_token":"num"}'


def test_cli_docker_command_reports_missing_docker(monkeypatch, capsys) -> None:
    repo_root = Path(__file__).resolve().parents[2]

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    code = cli.main(["docker-up"])

    assert code == 1
    err = capsys.readouterr().err
    assert "未找到 docker 可执行文件" in err


def test_cli_docker_up_reports_startup_failure_with_logs(monkeypatch, tmp_path, capsys) -> None:
    repo_root = tmp_path
    env_file = repo_root / ".openfish.docker.env"
    (repo_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    env_file.write_text("TELEGRAM_BOT_TOKEN=token\n", encoding="utf-8")

    def fake_run(command, check=False, cwd=None):  # noqa: ANN001, FBT002
        _ = check
        _ = cwd
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli, "_docker_env_file", lambda: env_file)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli, "_validate_telegram_token", lambda token: (True, ""))
    monkeypatch.setattr(cli, "_docker_wait_for_running_state", lambda docker_bin, timeout_seconds=8.0: "restarting")
    monkeypatch.setattr(cli, "_docker_recent_logs", lambda docker_bin, lines=40: "invalid token")
    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["docker-up"])

    assert code == 1
    err = capsys.readouterr().err
    assert "启动失败" in err
    assert "invalid token" in err


def test_cli_docker_login_codex_requires_running_container(monkeypatch, capsys) -> None:
    repo_root = Path(__file__).resolve().parents[2]

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli, "_docker_require_running", lambda docker_bin, command_name: False)

    code = cli.main(["docker-login-codex"])

    assert code == 1


def test_cli_docker_health_reports_ready(monkeypatch, tmp_path, capsys) -> None:
    repo_root = tmp_path
    env_file = repo_root / ".openfish.docker.env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=token\n", encoding="utf-8")
    (repo_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli, "_docker_env_file", lambda: env_file)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli, "_validate_telegram_token", lambda token: (True, ""))
    monkeypatch.setattr(cli, "_docker_container_state", lambda docker_bin: "running")
    monkeypatch.setattr(cli, "_docker_codex_login_status", lambda docker_bin: (True, "Logged in"))

    code = cli.main(["docker-health"])

    assert code == 0
    out = capsys.readouterr().out
    assert "Telegram Bot Token 有效" in out
    assert "openfish 容器运行中" in out
    assert "Codex 已登录" in out
    assert "[docker-health] ready" in out


def test_cli_docker_health_reports_missing_config(monkeypatch, tmp_path, capsys) -> None:
    repo_root = tmp_path
    (repo_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli, "_docker_env_file", lambda: repo_root / ".openfish.docker.env")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")

    code = cli.main(["docker-health"])

    assert code == 1
    out = capsys.readouterr().out
    assert "Docker 配置文件不存在" in out
    assert "openfish docker-configure" in out


def test_cli_docker_health_reports_codex_login_needed(monkeypatch, tmp_path, capsys) -> None:
    repo_root = tmp_path
    env_file = repo_root / ".openfish.docker.env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=token\n", encoding="utf-8")
    (repo_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(cli, "_docker_env_file", lambda: env_file)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(cli, "_validate_telegram_token", lambda token: (True, ""))
    monkeypatch.setattr(cli, "_docker_container_state", lambda docker_bin: "running")
    monkeypatch.setattr(cli, "_docker_codex_login_status", lambda docker_bin: (False, "Not logged in"))

    code = cli.main(["docker-health"])

    assert code == 1
    out = capsys.readouterr().out
    assert "Codex 未登录" in out
    assert "openfish docker-login-codex" in out


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


def test_cli_uninstall_stops_and_runs_pip(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(cli, "_native_stop", lambda: 0)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    def fake_run(command, check=False, cwd=None):  # noqa: ANN001, FBT002
        _ = check
        _ = cwd
        calls.append(list(command))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["uninstall"])

    assert code == 0
    assert calls == [[cli.sys.executable, "-m", "pip", "uninstall", "-y", "openfish"]]


def test_cli_uninstall_can_purge_runtime(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("OPENFISH_HOME", str(home))
    monkeypatch.setattr(cli, "_native_stop", lambda: 0)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text("X=1\n", encoding="utf-8")
    (home / "projects.yaml").write_text("version: 1\nprojects: {}\n", encoding="utf-8")
    (home / "data" / "app.db").write_text("", encoding="utf-8")

    def fake_run(command, check=False, cwd=None):  # noqa: ANN001, FBT002
        _ = check
        _ = cwd
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["uninstall", "--purge-runtime"])

    assert code == 0
    assert not (home / ".env").exists()
    assert not (home / "projects.yaml").exists()
    assert not (home / "data").exists()


def test_cli_uninstall_interactively_enables_runtime_purge(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("OPENFISH_HOME", str(home))
    monkeypatch.setattr(cli, "_native_stop", lambda: 0)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text("X=1\n", encoding="utf-8")
    (home / "projects.yaml").write_text("version: 1\nprojects: {}\n", encoding="utf-8")

    def fake_run(command, check=False, cwd=None):  # noqa: ANN001, FBT002
        _ = check
        _ = cwd
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    code = cli.main(["uninstall"])

    assert code == 0
    assert not (home / ".env").exists()
    assert not (home / "projects.yaml").exists()
    assert not (home / "data").exists()


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


def test_cli_configure_autodetects_telegram_user_id(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("OPENFISH_HOME", str(tmp_path / "home"))
    inputs = iter(
        [
            "",
            "/tmp/projects",
            "/tmp/projects/demo",
            "demo",
            "Demo",
        ]
    )

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "token-123")
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(cli, "_suggest_telegram_user_ids", lambda token: "123456789")

    code = cli.main(["configure"])

    assert code == 0
    out = capsys.readouterr().out
    assert "已自动探测 Telegram 用户 ID: 123456789" in out
    env_file = tmp_path / "home" / ".env"
    assert "ALLOWED_TELEGRAM_USER_IDS=123456789" in env_file.read_text(encoding="utf-8")


def test_cli_configure_uses_fixed_project_root_in_docker_mode(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("OPENFISH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENFISH_DOCKER_MODE", "1")
    inputs = iter(
        [
            "123456789",
            "/workspace/projects/demo",
            "demo",
            "Demo",
        ]
    )

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "token-123")
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(cli, "_suggest_telegram_user_ids", lambda token: None)
    original_mkdir = Path.mkdir

    def fake_mkdir(self, mode=0o777, parents=False, exist_ok=False):  # noqa: ANN001, FBT002
        if str(self).startswith("/workspace"):
            return None
        return original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    code = cli.main(["configure"])

    assert code == 0
    out = capsys.readouterr().out
    assert "默认项目根目录（Docker 固定）: /workspace/projects" in out
    env_file = tmp_path / "home" / ".env"
    assert "DEFAULT_PROJECT_ROOT=/workspace/projects" in env_file.read_text(encoding="utf-8")


def test_cli_dispatches_native_tg_user_id_with_args(monkeypatch) -> None:
    captured: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(cli, "_run_native_command", lambda command, args: captured.append((command, args)) or 0)

    code = cli.main(["tg-user-id", "alice"])

    assert code == 0
    assert captured == [("tg-user-id", ["alice"])]


def test_cli_update_check_reports_package_mode_without_git(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_supports_repo_updates", lambda: False)

    code = cli._native_update_check()

    assert code == 0
    out = capsys.readouterr().out
    assert "不是 git 仓库模式" in out


def test_cli_update_rejects_package_mode_without_git(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_supports_repo_updates", lambda: False)

    code = cli._native_update()

    assert code == 1
    err = capsys.readouterr().err
    assert "python -m pip install --upgrade openfish" in err


def test_cli_check_suggests_detected_user_ids(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("OPENFISH_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=token-123",
                "ALLOWED_TELEGRAM_USER_IDS=",
                "PROJECTS_CONFIG_PATH=./projects.yaml",
                "SQLITE_PATH=./data/app.db",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (home / "projects.yaml").write_text("version: 1\ndefault_project_root: ''\nprojects: {}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_suggest_telegram_user_ids", lambda token: "123456789")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: type("Resp", (), {"status_code": 200})())

    code = cli._native_check()

    assert code == 1
    out = capsys.readouterr().out
    assert "ALLOWED_TELEGRAM_USER_IDS=123456789" in out
