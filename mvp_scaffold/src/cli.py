"""Console entry point for OpenFish operational commands."""

from __future__ import annotations

import argparse
import getpass
from importlib import metadata, resources
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time

from src.update_service import UpdateService


SCRIPT_FORWARDED_COMMANDS: set[str] = set()

NATIVE_COMMANDS = {
    "install",
    "uninstall",
    "configure",
    "docker-configure",
    "install-start",
    "init-home",
    "check",
    "run",
    "start",
    "stop",
    "restart",
    "status",
    "logs",
    "logs-clear",
    "tg-user-id",
    "version",
    "update",
    "update-check",
}

DOCKER_COMMANDS = {
    "docker-up",
    "docker-down",
    "docker-logs",
    "docker-ps",
    "docker-login-codex",
    "docker-codex-status",
}

SUPPORTED_COMMANDS = SCRIPT_FORWARDED_COMMANDS | NATIVE_COMMANDS | DOCKER_COMMANDS


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _app_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _script_path() -> Path:
    return _app_root() / "scripts" / "install_start.sh"


def _package_resource(*parts: str) -> Path:
    path = resources.files("src")
    for part in ("resources", *parts):
        path = path.joinpath(part)
    return Path(str(path))


def _default_home_dir() -> Path:
    return Path(os.path.expanduser(os.environ.get("OPENFISH_HOME", "~/.config/openfish"))).resolve()


def _docker_mode() -> bool:
    return os.environ.get("OPENFISH_DOCKER_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def _docker_env_file() -> Path:
    return _repo_root() / ".openfish.docker.env"


def _repo_mode() -> bool:
    return (_repo_root() / ".git").exists()


def _env_file() -> Path:
    raw = os.environ.get("OPENFISH_ENV_FILE", "").strip()
    if raw:
        return Path(os.path.expanduser(raw)).resolve()
    if os.environ.get("OPENFISH_HOME", "").strip():
        return _default_home_dir() / ".env"
    if _repo_mode():
        return _repo_root() / ".env"
    return _default_home_dir() / ".env"


def _runtime_root() -> Path:
    if os.environ.get("OPENFISH_HOME", "").strip():
        return _default_home_dir()
    if os.environ.get("OPENFISH_ENV_FILE", "").strip():
        return _env_file().parent
    if _repo_mode():
        return _app_root()
    return _default_home_dir()


def _home_env_file() -> Path:
    return _default_home_dir() / ".env"


def _home_projects_file() -> Path:
    return _default_home_dir() / "projects.yaml"


def _data_dir() -> Path:
    env = _load_env_map()
    raw = env.get("SQLITE_PATH", "").strip()
    if raw:
        return _resolve_runtime_path(raw, repo_root=_repo_root(), app_root=_runtime_root()).parent
    return _runtime_root() / "data"


def _pid_file() -> Path:
    return _data_dir() / "app.pid"


def _log_dir() -> Path:
    env = _load_env_map()
    raw = env.get("LOG_DIR", "").strip()
    if raw:
        return _resolve_runtime_path(raw, repo_root=_repo_root(), app_root=_runtime_root())
    return _data_dir() / "logs"


def _log_file() -> Path:
    return _log_dir() / "app.out.log"


def _venv_python() -> str:
    repo_python = _app_root() / ".venv" / "bin" / "python"
    if repo_python.exists():
        return str(repo_python)
    return sys.executable


def _load_env_map() -> dict[str, str]:
    path = _env_file()
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        loaded[key] = os.path.expanduser(value)
    return loaded


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(_load_env_map())
    return env


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


def _service_cwd() -> Path:
    return _app_root() if _repo_mode() else _runtime_root()


def _render_home_env() -> str:
    bundled = _package_resource("env.example").read_text(encoding="utf-8")
    replacements = {
        "PROJECTS_CONFIG_PATH=./mvp_scaffold/projects.yaml": "PROJECTS_CONFIG_PATH=./projects.yaml",
        "SQLITE_PATH=./mvp_scaffold/data/app.db": "SQLITE_PATH=./data/app.db",
        "MIGRATIONS_DIR=./mvp_scaffold/migrations": f"MIGRATIONS_DIR={_package_resource('migrations')}",
        "OPENFISH_LOCK_PATH=./mvp_scaffold/data/openfish.lock": "OPENFISH_LOCK_PATH=./data/openfish.lock",
        "DATA_DIR=./mvp_scaffold/data": "DATA_DIR=./data",
        "LOG_DIR=./mvp_scaffold/data/logs": "LOG_DIR=./data/logs",
        "ARTIFACTS_DIR=./mvp_scaffold/data/artifacts": "ARTIFACTS_DIR=./data/artifacts",
        "SUMMARIES_DIR=./mvp_scaffold/data/summaries": "SUMMARIES_DIR=./data/summaries",
    }
    for source, target in replacements.items():
        bundled = bundled.replace(source, target)
    return bundled


def _native_init_home() -> int:
    runtime_root = _default_home_dir()
    env_file = _home_env_file()
    projects_file = _home_projects_file()
    for path in (
        runtime_root,
        runtime_root / "data",
        runtime_root / "data" / "logs",
        runtime_root / "data" / "artifacts",
        runtime_root / "data" / "summaries",
    ):
        path.mkdir(parents=True, exist_ok=True)
    if not env_file.exists():
        env_file.write_text(_render_home_env(), encoding="utf-8")
        print(f"[init-home] wrote {env_file}")
    else:
        print(f"[init-home] keep existing {env_file}")
    if not projects_file.exists():
        projects_file.write_text("version: 1\ndefault_project_root: ''\nprojects: {}\n", encoding="utf-8")
        print(f"[init-home] wrote {projects_file}")
    else:
        print(f"[init-home] keep existing {projects_file}")
    print("[init-home] export OPENFISH_HOME before start if you want to use this runtime home:")
    print(f"export OPENFISH_HOME={runtime_root}")
    print("openfish check")
    print("openfish start")
    return 0


def _template_text_for_runtime() -> str:
    if _runtime_root() == _default_home_dir():
        return _render_home_env()
    return _package_resource("env.example").read_text(encoding="utf-8")


def _projects_path_for_runtime() -> Path:
    env = _load_env_map()
    return _resolve_runtime_path(
        env.get("PROJECTS_CONFIG_PATH", "./projects.yaml"),
        repo_root=_repo_root(),
        app_root=_runtime_root(),
    )


def _replace_env_key(content: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    replacement = f"{key}={value}"
    if pattern.search(content):
        return pattern.sub(replacement, content)
    suffix = "" if content.endswith("\n") else "\n"
    return f"{content}{suffix}{replacement}\n"


def _render_env_file(overrides: dict[str, str]) -> str:
    content = _template_text_for_runtime()
    merged = _load_env_map()
    merged.update(overrides)
    for key, value in merged.items():
        content = _replace_env_key(content, key, value)
    return content


def _load_simple_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        loaded[key] = value
    return loaded


def _write_simple_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# OpenFish Docker runtime configuration"]
    for key in sorted(values):
        lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def _derive_project_key(raw: str) -> str:
    candidate = Path(raw).name.lower()
    candidate = re.sub(r"[^a-z0-9._-]+", "-", candidate).strip("-._")
    return candidate or "demo"


def _prompt_value(prompt: str, default: str = "", *, secret: bool = False, allow_empty: bool = False) -> str:
    label = f"{prompt} [{default}]" if default else prompt
    while True:
        value = getpass.getpass(f"{label}: ") if secret else input(f"{label}: ")
        value = value.strip() or default
        if allow_empty:
            return value
        if value:
            return value


def _native_install() -> int:
    _ensure_runtime_dirs()
    env_file = _env_file()
    projects_file = _projects_path_for_runtime()
    env_file.parent.mkdir(parents=True, exist_ok=True)
    projects_file.parent.mkdir(parents=True, exist_ok=True)
    if not env_file.exists():
        env_file.write_text(_template_text_for_runtime(), encoding="utf-8")
        print(f"[install] created {env_file}")
    else:
        print(f"[install] keep existing {env_file}")
    if not projects_file.exists():
        projects_file.write_text("version: 1\ndefault_project_root: ''\nprojects: {}\n", encoding="utf-8")
        print(f"[install] created {projects_file}")
    else:
        print(f"[install] keep existing {projects_file}")
    print("[install] next:")
    print("  openfish configure")
    print("  openfish check")
    return 0


def _purge_runtime_files() -> None:
    repo_root = _repo_root().resolve()
    runtime_root = _runtime_root().resolve()
    env_file = _env_file().resolve()
    projects_file = _projects_path_for_runtime().resolve()
    data_dir = _data_dir().resolve()

    candidates: list[Path] = []
    if data_dir.exists():
        candidates.append(data_dir)
    for path in (projects_file, env_file):
        if path.exists():
            candidates.append(path)

    removable: list[Path] = []
    for path in candidates:
        try:
            path.relative_to(repo_root)
            removable.append(path)
            continue
        except ValueError:
            pass
        try:
            path.relative_to(runtime_root)
            removable.append(path)
        except ValueError:
            continue

    for path in sorted(removable, key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def _native_uninstall(args: list[str]) -> int:
    purge_runtime = "--purge-runtime" in args
    if not purge_runtime and sys.stdin.isatty():
        choice = input("同时清理运行时配置和数据吗？ [y/N]: ").strip().lower()
        purge_runtime = choice in {"y", "yes"}

    stop_code = _native_stop()
    uninstall_cmd = [sys.executable, "-m", "pip", "uninstall", "-y", "openfish"]
    completed = subprocess.run(uninstall_cmd, check=False)  # noqa: S603
    if completed.returncode != 0:
        print("[uninstall] pip 卸载失败。", file=sys.stderr)
        print("[uninstall] 可手动执行: python -m pip uninstall openfish", file=sys.stderr)
        return int(completed.returncode)

    print("[uninstall] openfish 包已卸载。")
    if purge_runtime:
        _purge_runtime_files()
        print("[uninstall] 运行时配置与数据已清理。")
    else:
        print("[uninstall] 保留运行时配置与数据。")
        print(f"[uninstall] 如需手动清理，可删除: {_env_file()}")
        print(f"[uninstall] 如需手动清理，可删除: {_projects_path_for_runtime()}")
        print(f"[uninstall] 如需手动清理，可删除: {_data_dir()}")
    return stop_code


def _native_configure() -> int:
    if not sys.stdin.isatty():
        print("[configure] 需要交互终端。", file=sys.stderr)
        return 1

    env_file = _env_file()
    projects_file = _projects_path_for_runtime()
    current = _load_env_map()
    env_file.parent.mkdir(parents=True, exist_ok=True)
    projects_file.parent.mkdir(parents=True, exist_ok=True)

    print("[configure] 开始最小化配置向导")
    token_default = current.get("TELEGRAM_BOT_TOKEN", "")
    if token_default == "your_telegram_bot_token_here":
        token_default = ""
    token = _prompt_value("1/6 TELEGRAM_BOT_TOKEN", token_default, secret=True)

    ids_default = current.get("ALLOWED_TELEGRAM_USER_IDS", "")
    if not _is_valid_user_ids(ids_default):
        suggested_ids = _suggest_telegram_user_ids(token)
        if suggested_ids:
            ids_default = suggested_ids
            print(f"[configure] 已自动探测 Telegram 用户 ID: {suggested_ids}")
        else:
            print("[hint] 未自动探测到用户 ID。先给 bot 发一条私聊消息（例如 /start），再重试。")
    while True:
        allowed_ids = _prompt_value("2/6 Telegram 用户 ID（多个逗号分隔）", ids_default)
        if _is_valid_user_ids(allowed_ids):
            break
        print("[warn] 必须是纯数字 Telegram 用户 ID。")

    if _docker_mode():
        default_root = "/workspace/projects"
        print(f"3/6 默认项目根目录（Docker 固定）: {default_root}")
    else:
        default_root_default = current.get("DEFAULT_PROJECT_ROOT", os.path.expanduser("~/workspace/projects"))
        while True:
            default_root = _prompt_value("3/6 默认项目根目录（绝对路径）", default_root_default)
            if Path(os.path.expanduser(default_root)).is_absolute():
                break
            print("[warn] 默认项目根目录必须是绝对路径。")
    default_root_path = Path(os.path.expanduser(default_root))
    default_root_path.mkdir(parents=True, exist_ok=True)

    first_project_default = str(default_root_path / "demo")
    while True:
        first_project = _prompt_value("4/6 第一个项目目录（绝对路径）", first_project_default)
        first_project_path = Path(os.path.expanduser(first_project))
        if first_project_path.is_absolute():
            break
        print("[warn] 项目目录必须是绝对路径。")
    first_project_path.mkdir(parents=True, exist_ok=True)

    project_key_default = _derive_project_key(str(first_project_path))
    project_key = _prompt_value("5/6 项目 key", project_key_default)
    project_name = _prompt_value("6/6 项目显示名称", project_key)

    env_content = _render_env_file(
        {
            "TELEGRAM_BOT_TOKEN": token,
            "ALLOWED_TELEGRAM_USER_IDS": allowed_ids,
            "DEFAULT_PROJECT_ROOT": str(default_root_path),
        }
    )
    env_file.write_text(env_content, encoding="utf-8")
    projects_file.write_text(
        (
            "version: 1\n"
            f"default_project_root: '{default_root_path}'\n\n"
            "projects:\n"
            f"  {project_key}:\n"
            f"    name: '{project_name}'\n"
            f"    path: '{first_project_path}'\n"
            "    allowed_directories:\n"
            f"      - '{first_project_path}'\n"
        ),
        encoding="utf-8",
    )
    print(f"[configure] wrote {env_file}")
    print(f"[configure] wrote {projects_file}")
    print("[configure] next:")
    print("  openfish check")
    print("  openfish start")
    return 0


def _native_docker_configure() -> int:
    if not sys.stdin.isatty():
        print("[docker-configure] 需要交互终端。", file=sys.stderr)
        return 1

    env_file = _docker_env_file()
    current = _load_simple_env_file(env_file)
    print("[docker-configure] 开始 Docker 配置向导")

    token_default = current.get("TELEGRAM_BOT_TOKEN", "")
    if token_default == "your_telegram_bot_token_here":
        token_default = ""
    token = _prompt_value("1/5 TELEGRAM_BOT_TOKEN", token_default, secret=True)

    ids_default = current.get("ALLOWED_TELEGRAM_USER_IDS", "")
    if not _is_valid_user_ids(ids_default):
        suggested_ids = _suggest_telegram_user_ids(token)
        if suggested_ids:
            ids_default = suggested_ids
            print(f"[docker-configure] 已自动探测 Telegram 用户 ID: {suggested_ids}")
        else:
            print("[hint] 未自动探测到用户 ID。先给 bot 发一条私聊消息（例如 /start），再重试。")
    while True:
        allowed_ids = _prompt_value("2/5 Telegram 用户 ID（多个逗号分隔）", ids_default)
        if _is_valid_user_ids(allowed_ids):
            break
        print("[warn] 必须是纯数字 Telegram 用户 ID。")

    token_ok, token_error = _validate_telegram_token(token)
    if not token_ok:
        print(f"[docker-configure] Telegram Bot Token 校验失败: {token_error}", file=sys.stderr)
        return 1

    default_project = _prompt_value("3/5 默认项目 key（可留空）", current.get("DEFAULT_PROJECT", ""), allow_empty=True)
    bootstrap_key = _prompt_value("4/5 容器启动时预置项目 key（可留空）", current.get("OPENFISH_BOOTSTRAP_PROJECT_KEY", ""), allow_empty=True)
    bootstrap_name_default = current.get("OPENFISH_BOOTSTRAP_PROJECT_NAME", bootstrap_key)
    bootstrap_name = _prompt_value("5/5 预置项目显示名（可留空）", bootstrap_name_default, allow_empty=True)

    values = {
        "TELEGRAM_BOT_TOKEN": token,
        "ALLOWED_TELEGRAM_USER_IDS": allowed_ids,
        "DEFAULT_PROJECT_ROOT": "/workspace/projects",
        "OPENFISH_DOCKER_MODE": "1",
    }
    if default_project:
        values["DEFAULT_PROJECT"] = default_project
    if bootstrap_key:
        values["OPENFISH_BOOTSTRAP_PROJECT_KEY"] = bootstrap_key
    if bootstrap_name:
        values["OPENFISH_BOOTSTRAP_PROJECT_NAME"] = bootstrap_name
    _write_simple_env_file(env_file, values)
    print(f"[docker-configure] wrote {env_file}")
    print("[docker-configure] next:")
    print("  openfish docker-up")
    print("  openfish docker-login-codex")
    print("  openfish docker-codex-status")
    return 0


def _native_install_start() -> int:
    install_code = _native_install()
    if install_code != 0:
        return install_code
    return _native_start()


def _fetch_recent_telegram_users(token: str, filter_username: str = "") -> list[dict[str, str]]:
    import httpx

    response = httpx.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"limit": 100},
        timeout=25.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        description = payload.get("description") or "unknown error"
        raise RuntimeError(f"Telegram API error: {description}")

    users: dict[str, dict[str, str]] = {}

    def _collect_user(user_obj: object, chat_obj: object, update_id: object) -> None:
        if not isinstance(user_obj, dict):
            return
        user_id = user_obj.get("id")
        if user_id is None:
            return
        username = str(user_obj.get("username") or "").strip()
        full_name = " ".join(
            part.strip()
            for part in (str(user_obj.get("first_name") or ""), str(user_obj.get("last_name") or ""))
            if part.strip()
        ) or "-"
        chat_id = "-"
        chat_type = "-"
        if isinstance(chat_obj, dict):
            chat_id = str(chat_obj.get("id", "-"))
            chat_type = str(chat_obj.get("type", "-"))
        users[str(user_id)] = {
            "user_id": str(user_id),
            "username": username,
            "full_name": full_name,
            "chat_id": chat_id,
            "chat_type": chat_type,
            "update_id": str(update_id),
        }

    for update in payload.get("result") or []:
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id", "-")
        for key in ("message", "edited_message"):
            msg = update.get(key)
            if isinstance(msg, dict):
                _collect_user(msg.get("from"), msg.get("chat"), update_id)
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            _collect_user(callback.get("from"), None, update_id)
        inline_query = update.get("inline_query")
        if isinstance(inline_query, dict):
            _collect_user(inline_query.get("from"), None, update_id)

    rows = list(users.values())
    if filter_username:
        rows = [row for row in rows if row["username"].lower() == filter_username]
    rows.sort(key=lambda row: int(row["user_id"]))
    return rows


def _suggest_telegram_user_ids(token: str) -> str | None:
    if _is_placeholder_token(token):
        return None
    try:
        rows = _fetch_recent_telegram_users(token)
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    return ",".join(row["user_id"] for row in rows)


def _validate_telegram_token(token: str) -> tuple[bool, str]:
    if _is_placeholder_token(token):
        return False, "TELEGRAM_BOT_TOKEN 未配置"
    try:
        import httpx

        response = httpx.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=20.0,
        )
        if response.status_code != 200:
            return False, "Bot Token 无效或 Telegram API 不可达"
        payload = response.json()
        if not payload.get("ok"):
            return False, str(payload.get("description") or "Bot Token 无效")
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"Telegram API 校验失败: {exc}"


def _native_tg_user_id(args: list[str]) -> int:
    env = _load_env_map()
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if _is_placeholder_token(token):
        print("[error] TELEGRAM_BOT_TOKEN 未配置，请先执行 openfish configure。", file=sys.stderr)
        return 1
    filter_username = (args[0] if args else "").strip().lstrip("@").lower()
    try:
        rows = _fetch_recent_telegram_users(token, filter_username)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 调用 Telegram API 失败: {exc}", file=sys.stderr)
        return 1

    if not rows:
        if filter_username:
            print(f"[hint] 未找到用户名 @{filter_username} 的最近记录。", file=sys.stderr)
        else:
            print("[hint] 未找到可用用户记录。先给 bot 发一条私聊消息（例如 /start），再重试。", file=sys.stderr)
        return 1

    print("Telegram 用户ID（最近 getUpdates 结果）:")
    for row in rows:
        username = f"@{row['username']}" if row["username"] else "-"
        print(
            f"- user_id={row['user_id']} username={username} name={row['full_name']} "
            f"chat_id={row['chat_id']} chat_type={row['chat_type']} update_id={row['update_id']}"
        )
    ids = ",".join(row["user_id"] for row in rows)
    print("")
    print("可用于 .env 的配置：")
    print(f"ALLOWED_TELEGRAM_USER_IDS={ids}")
    return 0


def _configured_lock_path() -> Path:
    env = _load_env_map()
    raw = env.get("OPENFISH_LOCK_PATH", str(_data_dir() / "openfish.lock"))
    return _resolve_runtime_path(raw, repo_root=_repo_root(), app_root=_runtime_root())


def _supports_repo_updates() -> bool:
    return (_repo_root() / ".git").exists()


def _print_check_result(status: str, message: str) -> None:
    prefix = "ok   " if status == "ok" else "fail "
    print(f"[check] {prefix} {message}")


def _is_placeholder_token(token: str | None) -> bool:
    return not token or token == "your_telegram_bot_token_here"


def _is_valid_user_ids(value: str | None) -> bool:
    if not value:
        return False
    parts = [item.strip() for item in value.split(",")]
    return bool(parts) and all(item.isdigit() for item in parts if item)


def _native_check() -> int:
    env = _load_env_map()
    failures = 0
    print("[check] 开始首次运行自检")

    python_bin = Path(_venv_python())
    if python_bin.exists():
        _print_check_result("ok", f"Python 解释器可用: {python_bin}")
    else:
        _print_check_result("fail", "Python 解释器不可用")
        failures += 1

    env_file = _env_file()
    if env_file.exists():
        _print_check_result("ok", f".env 存在: {env_file}")
    else:
        _print_check_result("fail", f".env 不存在: {env_file}")
        failures += 1

    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if _is_placeholder_token(token):
        _print_check_result("fail", "TELEGRAM_BOT_TOKEN 未配置")
        failures += 1
    else:
        _print_check_result("ok", "TELEGRAM_BOT_TOKEN 已配置")

    allowed_ids = env.get("ALLOWED_TELEGRAM_USER_IDS", "").strip()
    if _is_valid_user_ids(allowed_ids):
        _print_check_result("ok", "ALLOWED_TELEGRAM_USER_IDS 已配置")
    else:
        _print_check_result("fail", "ALLOWED_TELEGRAM_USER_IDS 无效")
        suggested_ids = _suggest_telegram_user_ids(token)
        if suggested_ids:
            print(f"[check] hint  可直接写入 .env: ALLOWED_TELEGRAM_USER_IDS={suggested_ids}")
        failures += 1

    projects_path = _resolve_runtime_path(
        env.get("PROJECTS_CONFIG_PATH", "./projects.yaml"),
        repo_root=_repo_root(),
        app_root=_runtime_root(),
    )
    if projects_path.exists():
        _print_check_result("ok", f"projects.yaml 存在: {projects_path}")
    else:
        _print_check_result("fail", f"projects.yaml 不存在: {projects_path}")
        failures += 1

    migrations_dir = _resolve_runtime_path(
        env.get("MIGRATIONS_DIR", str(_package_resource("migrations"))),
        repo_root=_repo_root(),
        app_root=_runtime_root(),
    )
    if migrations_dir.exists():
        _print_check_result("ok", f"migrations 目录存在: {migrations_dir}")
    else:
        _print_check_result("fail", f"migrations 目录不存在: {migrations_dir}")
        failures += 1

    sqlite_path = _resolve_runtime_path(
        env.get("SQLITE_PATH", "./data/app.db"),
        repo_root=_repo_root(),
        app_root=_runtime_root(),
    )
    sqlite_dir = sqlite_path.parent
    sqlite_dir.mkdir(parents=True, exist_ok=True)
    try:
        handle, temp_path = tempfile.mkstemp(dir=str(sqlite_dir), prefix="openfish-check-", suffix=".tmp")
        os.close(handle)
        Path(temp_path).unlink(missing_ok=True)
        _print_check_result("ok", f"SQLite 目录可写: {sqlite_dir}")
    except OSError:
        _print_check_result("fail", f"SQLite 目录不可写: {sqlite_dir}")
        failures += 1

    codex_bin = env.get("CODEX_BIN", "codex")
    if shutil.which(codex_bin):
        _print_check_result("ok", f"Codex CLI 可用: {codex_bin}")
    else:
        _print_check_result("fail", f"Codex CLI 不可用: {codex_bin}")
        failures += 1

    try:
        import httpx  # noqa: F401
        import telegram  # noqa: F401
        import yaml  # noqa: F401

        _print_check_result("ok", "运行依赖可导入")
    except ImportError:
        _print_check_result("fail", "运行依赖缺失，请重新安装 OpenFish")
        failures += 1

    if projects_path.exists():
        try:
            import yaml

            data = yaml.safe_load(projects_path.read_text(encoding="utf-8")) or {}
            projects = data.get("projects") or {}
            valid_project = False
            if isinstance(projects, dict):
                for item in projects.values():
                    if not isinstance(item, dict):
                        continue
                    project_path = str(item.get("path") or "").strip()
                    if project_path and Path(project_path).expanduser().exists():
                        valid_project = True
                        break
            if valid_project:
                _print_check_result("ok", "至少存在 1 个可用项目，且路径存在")
            else:
                _print_check_result("fail", "项目配置为空或项目路径不存在")
                failures += 1
        except Exception:
            _print_check_result("fail", "projects.yaml 无法解析")
            failures += 1

    if not _is_placeholder_token(token):
        try:
            import httpx

            response = httpx.get(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=20.0,
            )
            if response.status_code == 200:
                _print_check_result("ok", "Telegram API 连通，Bot Token 可用")
            else:
                _print_check_result("fail", "Telegram API 不可达或 Bot Token 无效")
                failures += 1
        except Exception:
            _print_check_result("fail", "Telegram API 不可达或 Bot Token 无效")
            failures += 1

    if failures > 0:
        print(f"[check] 失败项: {failures}")
        print("[check] 先修复以上问题，再执行 start。")
        return 1

    print("[check] 通过。建议下一步：")
    print("  openfish start")
    print("  然后在 Telegram 私聊 bot，发送 /start")
    return 0


def _ensure_runtime_dirs() -> None:
    for path in (
        _data_dir(),
        _log_dir(),
        _data_dir() / "artifacts",
        _data_dir() / "summaries",
    ):
        path.mkdir(parents=True, exist_ok=True)


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_expected_service_process(pid: int) -> bool:
    try:
        proc = subprocess.run(  # noqa: S603
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    cmdline = (proc.stdout or "").strip()
    return bool(cmdline and "-m src.main" in cmdline)


def _read_pid_file() -> int | None:
    path = _pid_file()
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _remove_stale_pid_file() -> None:
    path = _pid_file()
    pid = _read_pid_file()
    if pid is None:
        if path.exists():
            path.unlink()
        return
    if not _is_pid_alive(pid):
        path.unlink(missing_ok=True)


def _cleanup_stale_lock_file() -> None:
    lock_path = _configured_lock_path()
    if not lock_path.exists():
        return
    try:
        payload = lock_path.read_text(encoding="utf-8")
    except OSError:
        return
    pid = None
    for token in payload.replace("{", " ").replace("}", " ").replace(",", " ").split():
        if token.isdigit():
            pid = int(token)
            break
    if pid is not None and _is_pid_alive(pid):
        return
    lock_path.unlink(missing_ok=True)


def _is_running() -> bool:
    pid = _read_pid_file()
    if pid is None:
        return False
    if not _is_pid_alive(pid):
        return False
    return _is_expected_service_process(pid)


def _wait_process_exit(pid: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while _is_pid_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
    return True


def _native_start() -> int:
    _ensure_runtime_dirs()
    _cleanup_stale_lock_file()
    _remove_stale_pid_file()
    if _is_running():
        pid = _read_pid_file()
        print(f"[start] already running (pid={pid})")
        return 0

    python_bin = Path(_venv_python())
    if not python_bin.exists():
        print("[error] virtual env not found. Run: openfish install", file=sys.stderr)
        return 1

    log_file = _log_file()
    env = _runtime_env()
    with log_file.open("ab") as handle:
        proc = subprocess.Popen(  # noqa: S603
            [str(python_bin), "-m", "src.main"],
            cwd=str(_service_cwd()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _pid_file().write_text(f"{proc.pid}\n", encoding="utf-8")
    time.sleep(1.0)
    if _is_running():
        print(f"[start] started (pid={proc.pid})")
        print(f"[start] log file: {log_file}")
        return 0
    _pid_file().unlink(missing_ok=True)
    print(f"[error] failed to start, check logs: {log_file}", file=sys.stderr)
    return 1


def _native_run() -> int:
    python_bin = Path(_venv_python())
    if not python_bin.exists():
        print("[error] virtual env not found. Run: openfish install", file=sys.stderr)
        return 1
    env = _runtime_env()
    completed = subprocess.run(  # noqa: S603
        [str(python_bin), "-m", "src.main"],
        cwd=str(_service_cwd()),
        env=env,
        check=False,
    )
    return int(completed.returncode)


def _native_stop() -> int:
    pid = _read_pid_file()
    if pid is None:
        print("[stop] not running")
        return 0
    if not _is_pid_alive(pid):
        print("[stop] process not found, cleaning stale pid file")
        _pid_file().unlink(missing_ok=True)
        return 0
    if not _is_expected_service_process(pid):
        print(f"[stop] pid={pid} is not OpenFish service process, refuse to kill. Cleaning pid file.")
        _pid_file().unlink(missing_ok=True)
        return 1

    print(f"[stop] stopping pid={pid}")
    with suppress_oserror():
        os.kill(pid, signal.SIGTERM)
    if _wait_process_exit(pid, 20):
        _pid_file().unlink(missing_ok=True)
        print("[stop] stopped")
        return 0

    print("[stop] graceful stop timeout (20s), sending SIGINT")
    with suppress_oserror():
        os.kill(pid, signal.SIGINT)
    if _wait_process_exit(pid, 5):
        _pid_file().unlink(missing_ok=True)
        print("[stop] stopped after SIGINT")
        return 0

    print("[stop] force stop with SIGKILL")
    with suppress_oserror():
        os.kill(pid, signal.SIGKILL)
    _pid_file().unlink(missing_ok=True)
    return 0


class suppress_oserror:  # noqa: N801
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        return isinstance(exc, OSError)


def _native_status() -> int:
    lock_path = _configured_lock_path()
    if _is_running():
        print(f"running (pid={_read_pid_file()})")
        print(f"log: {_log_file()}")
        print(f"lock: {lock_path}")
        return 0
    print("stopped")
    return 0


def _native_logs() -> int:
    service = UpdateService(repo_root=_repo_root(), script_path=_script_path(), log_dir=_log_dir())
    result = service.read_logs()
    print(result.text)
    return 0


def _native_logs_clear() -> int:
    service = UpdateService(repo_root=_repo_root(), script_path=_script_path(), log_dir=_log_dir())
    result = service.clear_logs()
    print(result.text)
    return 0


def _native_version() -> int:
    if _supports_repo_updates():
        service = UpdateService(repo_root=_repo_root(), script_path=_script_path(), log_dir=_log_dir())
        version = service.get_current_version()
        print(f"repo: {_repo_root()}")
        print(f"branch: {version.branch}")
        print(f"version: {version.version}")
        print(f"commit: {version.commit}")
        return 0
    try:
        package_version = metadata.version("openfish")
    except metadata.PackageNotFoundError:
        package_version = "unknown"
    print("mode: package")
    print(f"runtime: {_runtime_root()}")
    print(f"version: {package_version}")
    return 0


def _native_update_check() -> int:
    if not _supports_repo_updates():
        print("[update-check] 当前不是 git 仓库模式。")
        print("[update-check] 请使用: python -m pip install --upgrade openfish")
        return 0
    service = UpdateService(repo_root=_repo_root(), script_path=_script_path(), log_dir=_log_dir())
    result = service.check_for_updates()
    current = result.current
    if current is None or result.upstream_ref is None or result.upstream_commit is None:
        print(result.summary)
        return 0
    print(f"branch: {current.branch}")
    print(f"current: {current.version} ({current.commit})")
    print(f"upstream: {result.upstream_ref} ({result.upstream_commit})")
    print(f"behind: {result.behind_count}")
    print(f"ahead: {result.ahead_count}")
    if result.commits:
        print("[update-check] updates available:")
        for line in result.commits:
            print(line)
    else:
        print("[update-check] already up to date")
    return 0


def _native_update() -> int:
    if not _supports_repo_updates():
        print("[update] 当前不是 git 仓库模式，不能执行 git 自更新。", file=sys.stderr)
        print("[update] 请使用: python -m pip install --upgrade openfish", file=sys.stderr)
        return 1
    service = UpdateService(repo_root=_repo_root(), script_path=_script_path(), log_dir=_log_dir())
    result = service.trigger_update()
    print(result.summary)
    return 0


def _run_native_command(command: str, args: list[str]) -> int:
    if command == "install":
        return _native_install()
    if command == "uninstall":
        return _native_uninstall(args)
    if command == "configure":
        return _native_configure()
    if command == "docker-configure":
        return _native_docker_configure()
    if command == "install-start":
        return _native_install_start()
    if command == "init-home":
        return _native_init_home()
    if command == "check":
        return _native_check()
    if command == "start":
        return _native_start()
    if command == "run":
        return _native_run()
    if command == "stop":
        return _native_stop()
    if command == "restart":
        stop_code = _native_stop()
        start_code = _native_start()
        return start_code if start_code != 0 else stop_code
    if command == "status":
        return _native_status()
    if command == "logs":
        return _native_logs()
    if command == "logs-clear":
        return _native_logs_clear()
    if command == "tg-user-id":
        return _native_tg_user_id(args)
    if command == "version":
        return _native_version()
    if command == "update":
        return _native_update()
    if command == "update-check":
        return _native_update_check()
    raise RuntimeError(f"unsupported native command: {command}")


def _run_script_command(command: str, args: list[str]) -> int:
    script_path = _script_path()
    if not script_path.exists():
        raise RuntimeError(f"OpenFish runtime script not found: {script_path}")
    cmd = ["bash", str(script_path), command, *args]
    completed = subprocess.run(cmd, check=False)  # noqa: S603
    return int(completed.returncode)


def _docker_compose_base_cmd(docker_bin: str) -> list[str]:
    env_file = _docker_env_file()
    base = [docker_bin, "compose"]
    if env_file.exists():
        base.extend(["--env-file", str(env_file)])
    return base


def _import_codex_auth_into_container(docker_bin: str, auth_content: str) -> int:
    repo_root = _repo_root()
    cmd = [
        docker_bin,
        "exec",
        "-i",
        "openfish",
        "/bin/sh",
        "-lc",
        "mkdir -p /root/.codex && cat > /root/.codex/auth.json && chmod 600 /root/.codex/auth.json",
    ]
    completed = subprocess.run(  # noqa: S603
        cmd,
        check=False,
        cwd=str(repo_root),
        input=auth_content,
        text=True,
    )
    if completed.returncode != 0:
        print("[docker] 导入 auth.json 失败。", file=sys.stderr)
        return int(completed.returncode)
    status_cmd = [docker_bin, "exec", "openfish", "codex", "login", "status"]
    subprocess.run(status_cmd, check=False, cwd=str(repo_root))  # noqa: S603
    return 0


def _docker_login_codex(docker_bin: str, args: list[str]) -> int:
    repo_root = _repo_root()
    tty_flags = ["-it"] if sys.stdin.isatty() and sys.stdout.isatty() else []

    if args and args[0] == "--device-auth":
        cmd = [docker_bin, "exec", *tty_flags, "openfish", "codex", "login", "--device-auth", *args[1:]]
        completed = subprocess.run(cmd, check=False, cwd=str(repo_root))  # noqa: S603
        return int(completed.returncode)

    auth_path_arg = args[0] if args else ""
    if auth_path_arg:
        auth_path = Path(os.path.expanduser(auth_path_arg))
        if not auth_path.exists() or not auth_path.is_file():
            print(f"[docker] auth.json 路径不存在: {auth_path}", file=sys.stderr)
            return 1
        return _import_codex_auth_into_container(docker_bin, auth_path.read_text(encoding="utf-8"))

    default_auth_path = Path(os.path.expanduser("~/.codex/auth.json"))
    if sys.stdin.isatty():
        print("[docker-login-codex] 选择登录方式：")
        print("  1) device     通过 Codex 官方设备登录")
        if default_auth_path.exists():
            print(f"  2) path       导入本机 auth.json ({default_auth_path})")
        else:
            print("  2) path       导入本机 auth.json 路径")
        print("  3) paste      粘贴 auth.json 内容")
        choice = input("登录方式 [device/path/paste] (默认 device): ").strip().lower() or "device"
        if choice == "path":
            path_default = str(default_auth_path) if default_auth_path.exists() else ""
            auth_path_raw = _prompt_value("auth.json 路径", path_default)
            auth_path = Path(os.path.expanduser(auth_path_raw))
            if not auth_path.exists() or not auth_path.is_file():
                print(f"[docker] auth.json 路径不存在: {auth_path}", file=sys.stderr)
                return 1
            return _import_codex_auth_into_container(docker_bin, auth_path.read_text(encoding="utf-8"))
        if choice == "paste":
            auth_content = input("粘贴 auth.json 内容（单行 JSON）: ").strip()
            if not auth_content:
                print("[docker] auth.json 内容不能为空。", file=sys.stderr)
                return 1
            return _import_codex_auth_into_container(docker_bin, auth_content)

    cmd = [docker_bin, "exec", *tty_flags, "openfish", "codex", "login", "--device-auth"]
    completed = subprocess.run(cmd, check=False, cwd=str(repo_root))  # noqa: S603
    return int(completed.returncode)


def _run_docker_command(command: str, args: list[str]) -> int:
    repo_root = _repo_root()
    compose_file = repo_root / "docker-compose.yml"
    if not compose_file.exists():
        raise RuntimeError(f"Docker compose file not found: {compose_file}")
    docker_bin = shutil.which("docker")
    if not docker_bin:
        print("[docker] 未找到 docker 可执行文件。", file=sys.stderr)
        print("[docker] 请先安装 Docker Desktop 或将 docker 加入 PATH。", file=sys.stderr)
        return 1
    if command == "docker-login-codex":
        return _docker_login_codex(docker_bin, args)

    compose_base = _docker_compose_base_cmd(docker_bin)
    if command == "docker-up" and not _docker_env_file().exists():
        print(f"[docker] 未找到 Docker 配置文件: {_docker_env_file()}", file=sys.stderr)
        print("[docker] 先执行: openfish docker-configure", file=sys.stderr)
        return 1
    if command == "docker-up":
        docker_env = _load_simple_env_file(_docker_env_file())
        token_ok, token_error = _validate_telegram_token(docker_env.get("TELEGRAM_BOT_TOKEN", "").strip())
        if not token_ok:
            print(f"[docker] Telegram Bot Token 校验失败: {token_error}", file=sys.stderr)
            print("[docker] 先执行: openfish docker-configure", file=sys.stderr)
            return 1
    mapping = {
        "docker-up": [*compose_base, "up", "-d", "--build"],
        "docker-down": [*compose_base, "down"],
        "docker-logs": [*compose_base, "logs", "-f"],
        "docker-ps": [*compose_base, "ps"],
        "docker-codex-status": [docker_bin, "exec", "openfish", "codex", "login", "status"],
    }
    cmd = [*mapping[command], *args]
    try:
        completed = subprocess.run(cmd, check=False, cwd=str(repo_root))  # noqa: S603
    except FileNotFoundError:
        print("[docker] 无法执行 docker 命令。请检查 Docker 是否已正确安装。", file=sys.stderr)
        return 1
    return int(completed.returncode)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openfish",
        description="OpenFish CLI wrapper",
    )
    parser.add_argument("command", nargs="?", help="install/start/restart/status/...")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="additional command arguments")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)
    command = (ns.command or "").strip()
    if not command:
        parser.print_help(sys.stderr)
        return 2
    if command not in SUPPORTED_COMMANDS:
        parser.error(f"unsupported command: {command}")
    if command in DOCKER_COMMANDS:
        return _run_docker_command(command, ns.args)
    if command in NATIVE_COMMANDS:
        return _run_native_command(command, ns.args)
    return _run_script_command(command, ns.args)


if __name__ == "__main__":
    raise SystemExit(main())
