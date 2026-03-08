#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$APP_DIR/.." && pwd)"

VENV_DIR="$APP_DIR/.venv"
ENV_FILE="$REPO_ROOT/.env"
ENV_EXAMPLE="$REPO_ROOT/env.example"
PROJECTS_FILE="$APP_DIR/projects.yaml"
PROJECTS_EXAMPLE="$REPO_ROOT/projects.example.yaml"
PID_FILE="$APP_DIR/data/app.pid"
LOG_DIR="$APP_DIR/data/logs"
LOG_FILE="$LOG_DIR/app.out.log"
STOP_GRACE_SECONDS="${STOP_GRACE_SECONDS:-20}"
STOP_INT_GRACE_SECONDS="${STOP_INT_GRACE_SECONDS:-5}"

PYTHON_BIN="${PYTHON_BIN:-python3.12}"

usage() {
  cat <<'EOF'
Usage:
  bash mvp_scaffold/scripts/install_start.sh install
  bash mvp_scaffold/scripts/install_start.sh configure
  bash mvp_scaffold/scripts/install_start.sh check
  bash mvp_scaffold/scripts/install_start.sh version
  bash mvp_scaffold/scripts/install_start.sh update-check
  bash mvp_scaffold/scripts/install_start.sh update
  bash mvp_scaffold/scripts/install_start.sh logs-clear
  bash mvp_scaffold/scripts/install_start.sh tg-user-id [username]
  bash mvp_scaffold/scripts/install_start.sh start
  bash mvp_scaffold/scripts/install_start.sh run
  bash mvp_scaffold/scripts/install_start.sh stop
  bash mvp_scaffold/scripts/install_start.sh restart
  bash mvp_scaffold/scripts/install_start.sh status
  bash mvp_scaffold/scripts/install_start.sh logs
  bash mvp_scaffold/scripts/install_start.sh install-start

Commands:
  install       Create venv, install dependencies, and initialize local files.
  configure     Minimal interactive wizard for .env and the first project.
  check         Validate first-run prerequisites and Telegram connectivity.
  version       Show current git version and branch.
  update-check  Fetch upstream and show whether updates are available.
  update        Fast-forward update from GitHub, refresh deps, and restart if running.
  logs-clear    Truncate runtime and update logs.
  tg-user-id    Read Telegram getUpdates and print numeric user IDs.
  start         Start service in background (nohup) and write PID file.
  run           Run service in foreground (blocking).
  stop          Stop background process from PID file.
  restart       Restart background process.
  status        Show service status.
  logs          Tail runtime logs.
  install-start Run install then start.
EOF
}

ensure_python() {
  if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
    return
  fi
  echo "[error] Python 3.12+ not found. Set PYTHON_BIN or install python3.12." >&2
  exit 1
}

prepare_dirs() {
  mkdir -p "$APP_DIR/data" "$APP_DIR/data/logs" "$APP_DIR/data/artifacts" "$APP_DIR/data/summaries"
}

normalize_runtime_path() {
  local raw="$1"
  if [[ -z "$raw" ]]; then
    echo ""
    return
  fi
  if [[ "$raw" == /* ]]; then
    echo "$raw"
    return
  fi

  local cleaned="${raw#./}"
  if [[ "$cleaned" == mvp_scaffold/* ]]; then
    echo "$REPO_ROOT/$cleaned"
    return
  fi
  echo "$APP_DIR/$cleaned"
}

prompt_yes_no() {
  local prompt="$1"
  local default="${2:-y}"
  local answer=""
  local suffix="[Y/n]"
  if [[ "$default" == "n" ]]; then
    suffix="[y/N]"
  fi
  read -r -p "$prompt $suffix " answer
  answer="${answer:-$default}"
  case "${answer,,}" in
    y|yes) return 0 ;;
    n|no) return 1 ;;
    *) return 1 ;;
  esac
}

prompt_value() {
  local __result_var="$1"
  local prompt="$2"
  local default="${3:-}"
  local required="${4:-0}"
  local value=""

  while true; do
    if [[ -n "$default" ]]; then
      read -r -p "$prompt [$default]: " value
    else
      read -r -p "$prompt: " value
    fi
    value="${value:-$default}"

    if [[ "$required" == "1" && -z "$value" ]]; then
      echo "[warn] 此项必填，请重新输入。"
      continue
    fi
    break
  done
  printf -v "$__result_var" '%s' "$value"
}

prompt_secret() {
  local __result_var="$1"
  local prompt="$2"
  local default="${3:-}"
  local required="${4:-0}"
  local value=""

  while true; do
    if [[ -n "$default" ]]; then
      read -r -s -p "$prompt [留空保持当前值]: " value
    else
      read -r -s -p "$prompt: " value
    fi
    echo
    value="${value:-$default}"

    if [[ "$required" == "1" && -z "$value" ]]; then
      echo "[warn] 此项必填，请重新输入。"
      continue
    fi
    break
  done
  printf -v "$__result_var" '%s' "$value"
}

yaml_quote() {
  local value="$1"
  value="${value//\'/\'\'}"
  printf "'%s'" "$value"
}

write_env_file() {
  local token="$1"
  local allowed_ids="$2"
  local projects_path="$3"
  local default_project_root="$4"
  local sqlite_path="$5"
  local migrations_dir="$6"
  local codex_bin="$7"
  local log_level="$8"
  local poll_interval="$9"
  local msg_len="${10}"
  local timeout="${11}"
  local sandbox_mode="${12}"
  local approval_mode="${13}"
  local json_output="${14}"
  local enable_upload="${15}"
  local max_upload_size="${16}"
  local upload_temp_dir="${17}"
  local upload_extensions="${18}"

  cat > "$ENV_FILE" <<EOF
# Telegram
TELEGRAM_BOT_TOKEN=$token
ALLOWED_TELEGRAM_USER_IDS=$allowed_ids
TELEGRAM_POLL_INTERVAL_SECONDS=$poll_interval

# App paths
PROJECTS_CONFIG_PATH=$projects_path
DEFAULT_PROJECT_ROOT=$default_project_root
SQLITE_PATH=$sqlite_path
MIGRATIONS_DIR=$migrations_dir
OPENFISH_LOCK_PATH=./mvp_scaffold/data/openfish.lock
DATA_DIR=./mvp_scaffold/data
LOG_DIR=./mvp_scaffold/data/logs
ARTIFACTS_DIR=./mvp_scaffold/data/artifacts
SUMMARIES_DIR=./mvp_scaffold/data/summaries

# Codex
CODEX_BIN=$codex_bin
CODEX_DEFAULT_SANDBOX_MODE=$sandbox_mode
CODEX_DEFAULT_APPROVAL_MODE=$approval_mode
CODEX_JSON_OUTPUT=$json_output
CODEX_COMMAND_TIMEOUT_SECONDS=$timeout
CODEX_HOME=~/.codex
ENABLE_SKILL_INSTALL=true
SKILL_INSTALL_TIMEOUT_SECONDS=600

# App behavior
LOG_LEVEL=$log_level
DEFAULT_REPLY_LANGUAGE=zh-CN
DEFAULT_RISK_MODE=conservative
MAX_TELEGRAM_MESSAGE_LENGTH=$msg_len
ENABLE_TYPING_INDICATOR=true
ENABLE_PROGRESS_UPDATES=true
ENABLE_DOCUMENT_UPLOAD=$enable_upload
MAX_UPLOAD_SIZE_BYTES=$max_upload_size
UPLOAD_TEMP_DIR_NAME=$upload_temp_dir
ALLOWED_UPLOAD_EXTENSIONS=$upload_extensions

# Optional
DEFAULT_PROJECT=
EOF
}

derive_project_key() {
  local raw="$1"
  raw="$(basename "$raw")"
  raw="${raw,,}"
  raw="${raw// /-}"
  raw="$(printf '%s' "$raw" | tr -cd 'a-z0-9._-')"
  raw="${raw#[-._]}"
  raw="${raw%[-._]}"
  if [[ -z "$raw" ]]; then
    raw="demo"
  fi
  printf '%s' "$raw"
}

write_projects_file() {
  local default_project_root="$1"
  local project_key="$2"
  local project_name="$3"
  local project_path="$4"

  cat > "$PROJECTS_FILE" <<EOF
version: 1
default_project_root: $(yaml_quote "$default_project_root")

projects:
  $project_key:
    name: $(yaml_quote "$project_name")
    path: $(yaml_quote "$project_path")
    allowed_directories:
      - $(yaml_quote "$project_path")
EOF
}

print_check_result() {
  local status="$1"
  local message="$2"
  if [[ "$status" == "ok" ]]; then
    echo "[check] ok    $message"
  else
    echo "[check] fail  $message"
  fi
}

configure_wizard() {
  if [[ ! -t 0 ]]; then
    echo "[warn] configure 需要交互终端，当前为非交互环境。"
    return
  fi

  load_env
  echo "[configure] 开始最小化配置向导"
  echo "[configure] 仅需要 4 个关键项：Bot Token、Telegram 用户 ID、默认项目根目录、第一个项目目录。"
  echo "[configure] 如果你不知道用户 ID，先给 bot 发 /start，然后执行：bash mvp_scaffold/scripts/install_start.sh tg-user-id"

  local token_default="${TELEGRAM_BOT_TOKEN:-}"
  if [[ "$token_default" == "your_telegram_bot_token_here" ]]; then
    token_default=""
  fi
  local ids_default="${ALLOWED_TELEGRAM_USER_IDS:-}"
  if ! is_valid_user_ids "$ids_default"; then
    ids_default=""
  fi
  local default_root_default="${DEFAULT_PROJECT_ROOT:-$HOME/workspace/projects}"
  local first_project_default="$default_root_default/demo"
  local projects_path="./mvp_scaffold/projects.yaml"
  local sqlite_path="./mvp_scaffold/data/app.db"
  local migrations_dir="./mvp_scaffold/migrations"
  local codex_bin="${CODEX_BIN:-codex}"
  local log_level="${LOG_LEVEL:-INFO}"
  local poll_interval="${TELEGRAM_POLL_INTERVAL_SECONDS:-2}"
  local msg_len="${MAX_TELEGRAM_MESSAGE_LENGTH:-3500}"
  local timeout="${CODEX_COMMAND_TIMEOUT_SECONDS:-1800}"
  local sandbox_mode="${CODEX_DEFAULT_SANDBOX_MODE:-workspace-write}"
  local approval_mode="${CODEX_DEFAULT_APPROVAL_MODE:-never}"
  local json_output="${CODEX_JSON_OUTPUT:-true}"
  local enable_upload="${ENABLE_DOCUMENT_UPLOAD:-true}"
  local max_upload_size="${MAX_UPLOAD_SIZE_BYTES:-209715200}"
  local upload_temp_dir="${UPLOAD_TEMP_DIR_NAME:-.codex_telegram_uploads}"
  local upload_extensions="${ALLOWED_UPLOAD_EXTENSIONS:-txt,md,markdown,json,yaml,yml,xml,csv,log,ini,toml,py,js,ts,tsx,jsx,go,rs,java,kt,swift,sql,html,css,apk,zip}"

  local token ids default_project_root first_project_path first_project_key first_project_name derived_key
  prompt_secret token "1/4 请输入 TELEGRAM_BOT_TOKEN" "$token_default" 1
  while true; do
    prompt_value ids "2/4 请输入 Telegram 用户 ID（多个逗号分隔）" "$ids_default" 1
    if is_valid_user_ids "$ids"; then
      break
    fi
    echo "[warn] 必须是纯数字 Telegram 用户 ID。若不知道，可先运行 tg-user-id。"
    ids_default=""
  done

  while true; do
    prompt_value default_project_root "3/4 默认项目根目录（后续 /project-add 默认建在这里）" "$default_root_default" 1
    if [[ "$default_project_root" != /* ]]; then
      echo "[warn] 默认项目根目录必须是绝对路径。"
      continue
    fi
    mkdir -p "$default_project_root"
    break
  done

  while true; do
    prompt_value first_project_path "4/4 第一个项目目录（绝对路径）" "$first_project_default" 1
    if [[ "$first_project_path" != /* ]]; then
      echo "[warn] 项目目录必须是绝对路径。"
      continue
    fi
    if [[ ! -d "$first_project_path" ]]; then
      if prompt_yes_no "目录不存在，是否现在创建？" "y"; then
        mkdir -p "$first_project_path"
      else
        continue
      fi
    fi
    break
  done

  derived_key="$(derive_project_key "$first_project_path")"
  while true; do
    prompt_value first_project_key "项目 key（用于 /use）" "$derived_key" 1
    if [[ "$first_project_key" =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
      break
    fi
    echo "[warn] 项目 key 只允许字母数字/._-，长度 1-64。"
  done
  prompt_value first_project_name "项目显示名称" "$first_project_key" 1

  write_env_file \
    "$token" "$ids" "$projects_path" "$default_project_root" "$sqlite_path" "$migrations_dir" "$codex_bin" \
    "$log_level" "$poll_interval" "$msg_len" "$timeout" "$sandbox_mode" "$approval_mode" "$json_output" \
    "$enable_upload" "$max_upload_size" "$upload_temp_dir" "$upload_extensions"
  echo "[configure] 已写入 ${ENV_FILE}"

  local should_write_project="y"
  if [[ -f "$PROJECTS_FILE" ]]; then
    if prompt_yes_no "检测到 ${PROJECTS_FILE}，是否覆盖为最小化单项目配置？" "n"; then
      should_write_project="y"
    else
      should_write_project="n"
    fi
  fi

  if [[ "$should_write_project" == "y" ]]; then
    write_projects_file "$default_project_root" "$first_project_key" "$first_project_name" "$first_project_path"
    echo "[configure] 已写入 ${PROJECTS_FILE}"
  else
    echo "[configure] 保留现有 ${PROJECTS_FILE}"
  fi

  echo "[configure] 配置完成。建议按顺序执行："
  echo "  bash mvp_scaffold/scripts/install_start.sh check"
  echo "  bash mvp_scaffold/scripts/install_start.sh start"
  echo "  bash mvp_scaffold/scripts/install_start.sh logs"
  echo "[configure] 然后在 Telegram 私聊 bot，发送 /start"

  if prompt_yes_no "是否立即执行首次自检？" "y"; then
    check_runtime || true
  fi
}

install_deps() {
  ensure_python
  prepare_dirs

  if [[ ! -d "$VENV_DIR" ]]; then
    echo "[install] creating virtual environment at $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  echo "[install] upgrading pip"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip

  echo "[install] installing dependencies"
  "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

  if [[ ! -f "$PROJECTS_FILE" ]]; then
    if [[ -f "$PROJECTS_EXAMPLE" ]]; then
      echo "[install] creating $PROJECTS_FILE from projects.example.yaml"
      cp "$PROJECTS_EXAMPLE" "$PROJECTS_FILE"
    else
      echo "[warn] projects.example.yaml not found, please create $PROJECTS_FILE manually"
    fi
  fi

  if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$ENV_EXAMPLE" ]]; then
      echo "[install] creating $ENV_FILE from env.example"
      cp "$ENV_EXAMPLE" "$ENV_FILE"
      cat <<EOF
[next] 已生成 $ENV_FILE 样例。
[next] 推荐继续执行：
  bash mvp_scaffold/scripts/install_start.sh configure
EOF
    else
      echo "[warn] env.example not found, please create $ENV_FILE manually"
    fi
  fi

  if [[ "${SKIP_CONFIG_WIZARD:-0}" != "1" && -t 0 ]]; then
    if prompt_yes_no "是否立即运行最小化配置向导（推荐）？" "y"; then
      configure_wizard
    fi
  fi
}

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

is_placeholder_token() {
  [[ -z "${TELEGRAM_BOT_TOKEN:-}" || "${TELEGRAM_BOT_TOKEN:-}" == "your_telegram_bot_token_here" ]]
}

is_valid_user_ids() {
  local value="$1"
  [[ "$value" =~ ^[[:space:]]*[0-9]+([[:space:]]*,[[:space:]]*[0-9]+)*[[:space:]]*$ ]]
}

get_telegram_user_id() {
  load_env
  local filter_username="${1:-}"
  local token="${TELEGRAM_BOT_TOKEN:-}"
  local api_url=""
  local response=""
  local pybin=""

  if [[ -z "$token" || "$token" == "your_telegram_bot_token_here" ]]; then
    echo "[error] TELEGRAM_BOT_TOKEN 未配置，请先运行 configure。" >&2
    exit 1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    echo "[error] curl 不可用，无法调用 Telegram API。" >&2
    exit 1
  fi
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    pybin="$VENV_DIR/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    pybin="python3"
  elif command -v python >/dev/null 2>&1; then
    pybin="python"
  else
    echo "[error] python 解释器不可用，无法解析 Telegram JSON 响应。" >&2
    exit 1
  fi

  api_url="https://api.telegram.org/bot${token}/getUpdates?limit=100"
  if ! response="$(curl -fsS --connect-timeout 8 --max-time 25 "$api_url")"; then
    echo "[error] 调用 Telegram API 失败。请检查网络或 Bot Token。" >&2
    exit 1
  fi

  if ! TELEGRAM_JSON="$response" "$pybin" - "$filter_username" <<'PY'
import json
import os
import sys

filter_username = (sys.argv[1] or "").strip().lstrip("@").lower()
raw = (os.environ.get("TELEGRAM_JSON") or "").strip()
if not raw:
    print("[error] Telegram API 返回为空。")
    raise SystemExit(1)

try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    print("[error] Telegram API 返回无法解析为 JSON。")
    raise SystemExit(1)

if not payload.get("ok"):
    desc = payload.get("description") or "unknown error"
    print(f"[error] Telegram API error: {desc}")
    raise SystemExit(1)

updates = payload.get("result") or []
users = {}

def collect_user(user_obj, chat_obj, update_id):
    if not isinstance(user_obj, dict):
        return
    user_id = user_obj.get("id")
    if user_id is None:
        return
    username = (user_obj.get("username") or "").strip()
    first_name = (user_obj.get("first_name") or "").strip()
    last_name = (user_obj.get("last_name") or "").strip()
    full_name = (first_name + " " + last_name).strip() or "-"
    chat_id = "-"
    chat_type = "-"
    if isinstance(chat_obj, dict):
        chat_id = chat_obj.get("id", "-")
        chat_type = chat_obj.get("type", "-")
    key = str(user_id)
    users[key] = {
        "user_id": str(user_id),
        "username": username,
        "full_name": full_name,
        "chat_id": str(chat_id),
        "chat_type": str(chat_type),
        "update_id": str(update_id),
    }

for upd in updates:
    if not isinstance(upd, dict):
        continue
    update_id = upd.get("update_id", "-")
    msg = upd.get("message")
    if isinstance(msg, dict):
        collect_user(msg.get("from"), msg.get("chat"), update_id)
    edited = upd.get("edited_message")
    if isinstance(edited, dict):
        collect_user(edited.get("from"), edited.get("chat"), update_id)
    callback = upd.get("callback_query")
    if isinstance(callback, dict):
        collect_user(callback.get("from"), None, update_id)
    inline_query = upd.get("inline_query")
    if isinstance(inline_query, dict):
        collect_user(inline_query.get("from"), None, update_id)

rows = list(users.values())
if filter_username:
    rows = [r for r in rows if r["username"].lower() == filter_username]

rows.sort(key=lambda r: int(r["user_id"]))
if not rows:
    if filter_username:
        print(f"[hint] 未找到用户名 @{filter_username} 的最近记录。")
    else:
        print("[hint] 未找到可用用户记录。先给 bot 发一条私聊消息（例如 /start），再重试。")
    raise SystemExit(1)

print("Telegram 用户ID（最近 getUpdates 结果）:")
for r in rows:
    uname = f"@{r['username']}" if r["username"] else "-"
    print(
        f"- user_id={r['user_id']} username={uname} name={r['full_name']} "
        f"chat_id={r['chat_id']} chat_type={r['chat_type']} update_id={r['update_id']}"
    )

ids = ",".join(r["user_id"] for r in rows)
print("")
print("可用于 .env 的配置：")
print(f"ALLOWED_TELEGRAM_USER_IDS={ids}")
PY
  then
    exit 1
  fi
}

validate_runtime_config() {
  load_env

  export PROJECTS_CONFIG_PATH="$(normalize_runtime_path "${PROJECTS_CONFIG_PATH:-./projects.yaml}")"
  export SQLITE_PATH="$(normalize_runtime_path "${SQLITE_PATH:-./data/app.db}")"
  export MIGRATIONS_DIR="$(normalize_runtime_path "${MIGRATIONS_DIR:-./migrations}")"
  export OPENFISH_LOCK_PATH="$(normalize_runtime_path "${OPENFISH_LOCK_PATH:-./data/openfish.lock}")"
  export CODEX_BIN="${CODEX_BIN:-codex}"

  if is_placeholder_token; then
    echo "[error] TELEGRAM_BOT_TOKEN is missing or placeholder in $ENV_FILE" >&2
    exit 1
  fi
  if [[ -z "${ALLOWED_TELEGRAM_USER_IDS:-}" ]]; then
    echo "[error] ALLOWED_TELEGRAM_USER_IDS is missing in $ENV_FILE" >&2
    exit 1
  fi
  if ! is_valid_user_ids "${ALLOWED_TELEGRAM_USER_IDS:-}"; then
    echo "[error] ALLOWED_TELEGRAM_USER_IDS must be numeric IDs separated by commas." >&2
    exit 1
  fi
  if [[ ! -f "$PROJECTS_CONFIG_PATH" ]]; then
    echo "[error] projects config not found: $PROJECTS_CONFIG_PATH" >&2
    exit 1
  fi
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "[error] virtual env not found. Run: ... install_start.sh install" >&2
    exit 1
  fi
}

check_runtime() {
  load_env

  local failures=0
  local projects_path
  local sqlite_path
  local migrations_dir
  local codex_bin
  local pybin=""
  local check_tmp=""

  projects_path="$(normalize_runtime_path "${PROJECTS_CONFIG_PATH:-./mvp_scaffold/projects.yaml}")"
  sqlite_path="$(normalize_runtime_path "${SQLITE_PATH:-./mvp_scaffold/data/app.db}")"
  migrations_dir="$(normalize_runtime_path "${MIGRATIONS_DIR:-./mvp_scaffold/migrations}")"
  codex_bin="${CODEX_BIN:-codex}"

  echo "[check] 开始首次运行自检"

  if [[ -x "$VENV_DIR/bin/python" ]]; then
    print_check_result ok "Python 虚拟环境可用: $VENV_DIR"
    pybin="$VENV_DIR/bin/python"
  else
    print_check_result fail "Python 虚拟环境不存在，请先执行 install"
    failures=$((failures + 1))
  fi

  if [[ -f "$ENV_FILE" ]]; then
    print_check_result ok ".env 存在: $ENV_FILE"
  else
    print_check_result fail ".env 不存在，请先执行 configure"
    failures=$((failures + 1))
  fi

  if is_placeholder_token; then
    print_check_result fail "TELEGRAM_BOT_TOKEN 未配置"
    failures=$((failures + 1))
  else
    print_check_result ok "TELEGRAM_BOT_TOKEN 已配置"
  fi

  if is_valid_user_ids "${ALLOWED_TELEGRAM_USER_IDS:-}"; then
    print_check_result ok "ALLOWED_TELEGRAM_USER_IDS 已配置"
  else
    print_check_result fail "ALLOWED_TELEGRAM_USER_IDS 无效"
    failures=$((failures + 1))
  fi

  if [[ -f "$projects_path" ]]; then
    print_check_result ok "projects.yaml 存在: $projects_path"
  else
    print_check_result fail "projects.yaml 不存在: $projects_path"
    failures=$((failures + 1))
  fi

  if [[ -d "$migrations_dir" ]]; then
    print_check_result ok "migrations 目录存在: $migrations_dir"
  else
    print_check_result fail "migrations 目录不存在: $migrations_dir"
    failures=$((failures + 1))
  fi

  mkdir -p "$(dirname "$sqlite_path")"
  if check_tmp="$(mktemp "${sqlite_path}.check.XXXXXX" 2>/dev/null)"; then
    rm -f "$check_tmp"
    print_check_result ok "SQLite 目录可写: $(dirname "$sqlite_path")"
  else
    print_check_result fail "SQLite 目录不可写: $(dirname "$sqlite_path")"
    failures=$((failures + 1))
  fi

  if command -v "$codex_bin" >/dev/null 2>&1; then
    print_check_result ok "Codex CLI 可用: $codex_bin"
  else
    print_check_result fail "Codex CLI 不可用: $codex_bin"
    failures=$((failures + 1))
  fi

  if [[ -n "$pybin" ]]; then
    if "$pybin" - <<'PY' >/dev/null 2>&1
import importlib
mods = ["telegram", "yaml", "httpx"]
for mod in mods:
    importlib.import_module(mod)
PY
    then
      print_check_result ok "运行依赖可导入"
    else
      print_check_result fail "运行依赖缺失，请重新执行 install"
      failures=$((failures + 1))
    fi

    if [[ -f "$projects_path" ]]; then
      if PROJECTS_PATH="$projects_path" "$pybin" - <<'PY' >/dev/null 2>&1
from pathlib import Path
import os
import sys
import yaml

path = Path(os.environ["PROJECTS_PATH"])
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
projects = data.get("projects") or {}
if not isinstance(projects, dict) or not projects:
    raise SystemExit(1)
for item in projects.values():
    if not isinstance(item, dict):
        raise SystemExit(1)
    project_path = item.get("path")
    if not project_path or not Path(project_path).expanduser().exists():
        raise SystemExit(1)
PY
      then
        print_check_result ok "至少存在 1 个可用项目，且路径存在"
      else
        print_check_result fail "项目配置为空或项目路径不存在"
        failures=$((failures + 1))
      fi
    fi
  fi

  if ! is_placeholder_token && command -v curl >/dev/null 2>&1; then
    if curl -fsS --connect-timeout 8 --max-time 20 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" >/dev/null 2>&1; then
      print_check_result ok "Telegram API 连通，Bot Token 可用"
    else
      print_check_result fail "Telegram API 不可达或 Bot Token 无效"
      failures=$((failures + 1))
    fi
  else
    print_check_result fail "curl 不可用，无法验证 Telegram API"
    failures=$((failures + 1))
  fi

  if (( failures > 0 )); then
    echo "[check] 失败项: $failures"
    echo "[check] 先修复以上问题，再执行 start。"
    return 1
  fi

  echo "[check] 通过。建议下一步："
  echo "  bash mvp_scaffold/scripts/install_start.sh start"
  echo "  然后在 Telegram 私聊 bot，发送 /start"
  return 0
}

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return 1
  fi
  _is_expected_service_process "$pid"
}

lock_file_path() {
  load_env
  normalize_runtime_path "${OPENFISH_LOCK_PATH:-./data/openfish.lock}"
}

read_lock_pid() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  grep -Eo '"pid"[[:space:]]*:[[:space:]]*[0-9]+' "$file" | head -n 1 | grep -Eo '[0-9]+' || true
  return 0
}

cleanup_stale_lock_file() {
  local lock_file="$1"
  local lock_pid=""

  [[ -f "$lock_file" ]] || return 0
  lock_pid="$(read_lock_pid "$lock_file")"
  if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" >/dev/null 2>&1; then
    return 0
  fi

  echo "[start] removing stale lock file: $lock_file"
  rm -f "$lock_file"
}

_is_expected_service_process() {
  local pid="$1"
  local cmdline=""
  cmdline="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ -n "$cmdline" ]] || return 1
  [[ "$cmdline" == *"-m src.main"* ]]
}

_wait_process_exit() {
  local pid="$1"
  local grace_seconds="$2"
  local deadline=$((SECONDS + grace_seconds))

  while kill -0 "$pid" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      return 1
    fi
    sleep 0.2
  done
  return 0
}

start_bg() {
  validate_runtime_config
  prepare_dirs
  local lock_file
  local lock_pid=""
  lock_file="$(lock_file_path)"

  if is_running; then
    echo "[start] already running (pid=$(cat "$PID_FILE"))"
    return
  fi
  cleanup_stale_lock_file "$lock_file"
  lock_pid="$(read_lock_pid "$lock_file")"
  if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" >/dev/null 2>&1; then
    echo "[start] OpenFish lock already held (pid=$lock_pid)"
    echo "[start] lock file: $lock_file"
    return 1
  fi
  if [[ -f "$PID_FILE" ]]; then
    echo "[start] removing stale pid file: $PID_FILE"
    rm -f "$PID_FILE"
  fi

  echo "[start] launching in background"
  (
    cd "$APP_DIR"
    nohup "$VENV_DIR/bin/python" -m src.main </dev/null >>"$LOG_FILE" 2>&1 &
    local child_pid="$!"
    disown "$child_pid" 2>/dev/null || true
    echo "$child_pid" > "$PID_FILE"
  )

  sleep 1
  if is_running; then
    echo "[start] started (pid=$(cat "$PID_FILE"))"
    echo "[start] log file: $LOG_FILE"
  else
    rm -f "$PID_FILE"
    echo "[error] failed to start, check logs: $LOG_FILE" >&2
    tail -n 40 "$LOG_FILE" >&2 || true
    exit 1
  fi
}

run_fg() {
  validate_runtime_config
  prepare_dirs
  cd "$APP_DIR"
  exec "$VENV_DIR/bin/python" -m src.main
}

stop_bg() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "[stop] not running"
    return
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  if [[ -z "$pid" ]]; then
    echo "[stop] empty pid file, cleaning up"
    rm -f "$PID_FILE"
    return
  fi
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    echo "[stop] process not found, cleaning stale pid file"
    rm -f "$PID_FILE"
    return
  fi
  if ! _is_expected_service_process "$pid"; then
    echo "[stop] pid=$pid is not OpenFish service process, refuse to kill. Cleaning pid file."
    rm -f "$PID_FILE"
    return
  fi

  echo "[stop] stopping pid=$pid"
  kill -TERM "$pid" >/dev/null 2>&1 || true

  if _wait_process_exit "$pid" "$STOP_GRACE_SECONDS"; then
    rm -f "$PID_FILE"
    echo "[stop] stopped"
    return
  fi

  echo "[stop] graceful stop timeout (${STOP_GRACE_SECONDS}s), sending SIGINT"
  kill -INT "$pid" >/dev/null 2>&1 || true
  if _wait_process_exit "$pid" "$STOP_INT_GRACE_SECONDS"; then
    rm -f "$PID_FILE"
    echo "[stop] stopped after SIGINT"
    return
  fi

  echo "[stop] force stop with SIGKILL"
  kill -KILL "$pid" >/dev/null 2>&1 || true
  rm -f "$PID_FILE"
}

status_bg() {
  local lock_file
  local lock_pid=""
  lock_file="$(lock_file_path)"
  if is_running; then
    echo "running (pid=$(cat "$PID_FILE"))"
    echo "log: $LOG_FILE"
    echo "lock: $lock_file"
  elif [[ -f "$lock_file" ]] && lock_pid="$(read_lock_pid "$lock_file")" && [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" >/dev/null 2>&1; then
    echo "running (lock pid=$lock_pid, pid file missing)"
    echo "log: $LOG_FILE"
    echo "lock: $lock_file"
  else
    echo "stopped"
  fi
}

tail_logs() {
  prepare_dirs
  touch "$LOG_FILE"
  tail -f "$LOG_FILE"
}

clear_logs() {
  prepare_dirs
  : > "$LOG_FILE"
  : > "$LOG_DIR/update.log"
  echo "[logs-clear] cleared:"
  echo "  $LOG_FILE"
  echo "  $LOG_DIR/update.log"
}

ensure_git_repo() {
  if ! command -v git >/dev/null 2>&1; then
    echo "[error] git 不可用。" >&2
    exit 1
  fi
  if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "[error] 当前目录不是 Git 仓库: $REPO_ROOT" >&2
    exit 1
  fi
}

git_upstream_ref() {
  git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null
}

show_version() {
  ensure_git_repo
  local branch
  local version
  local head
  branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  version="$(git -C "$REPO_ROOT" describe --tags --always --dirty 2>/dev/null || echo unknown)"
  head="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "repo: $REPO_ROOT"
  echo "branch: $branch"
  echo "version: $version"
  echo "commit: $head"
}

update_check() {
  ensure_git_repo
  local upstream
  local current_branch
  local current_version
  local current_head
  local upstream_head
  local behind
  local ahead

  upstream="$(git_upstream_ref)"
  if [[ -z "$upstream" ]]; then
    echo "[error] 当前分支未配置上游分支。" >&2
    exit 1
  fi

  current_branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
  current_version="$(git -C "$REPO_ROOT" describe --tags --always --dirty)"
  current_head="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"

  echo "[update-check] fetching $upstream"
  git -C "$REPO_ROOT" fetch --quiet "${upstream%%/*}" "${upstream#*/}"

  upstream_head="$(git -C "$REPO_ROOT" rev-parse --short "$upstream")"
  behind="$(git -C "$REPO_ROOT" rev-list --count HEAD.."$upstream")"
  ahead="$(git -C "$REPO_ROOT" rev-list --count "$upstream"..HEAD)"

  echo "branch: $current_branch"
  echo "current: $current_version ($current_head)"
  echo "upstream: $upstream ($upstream_head)"
  echo "behind: $behind"
  echo "ahead: $ahead"

  if [[ "$behind" == "0" ]]; then
    echo "[update-check] already up to date"
    return 0
  fi

  echo "[update-check] updates available:"
  git -C "$REPO_ROOT" log --oneline --no-decorate HEAD.."$upstream" | head -n 5
}

update_now() {
  ensure_git_repo
  validate_runtime_config

  local upstream
  local remote_name
  local remote_branch
  local old_head
  local new_head
  local behind
  local running_before=0

  upstream="$(git_upstream_ref)"
  if [[ -z "$upstream" ]]; then
    echo "[error] 当前分支未配置上游分支，无法自动更新。" >&2
    exit 1
  fi
  remote_name="${upstream%%/*}"
  remote_branch="${upstream#*/}"

  if [[ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)" ]]; then
    echo "[error] 工作区存在未提交改动，拒绝自动更新。请先提交或清理后重试。" >&2
    exit 1
  fi

  if is_running; then
    running_before=1
  fi

  echo "[update] fetching $upstream"
  git -C "$REPO_ROOT" fetch --quiet "$remote_name" "$remote_branch"

  behind="$(git -C "$REPO_ROOT" rev-list --count "HEAD..$upstream")"
  if [[ "$behind" == "0" ]]; then
    echo "[update] already up to date"
    return 0
  fi

  old_head="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  echo "[update] pulling latest changes"
  git -C "$REPO_ROOT" pull --ff-only "$remote_name" "$remote_branch"
  new_head="$(git -C "$REPO_ROOT" rev-parse HEAD)"

  if git -C "$REPO_ROOT" diff --name-only "$old_head" "$new_head" | grep -Eq '(^|/)requirements\.txt$'; then
    echo "[update] requirements changed, refreshing dependencies"
    "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
  fi

  if [[ "$running_before" == "1" ]]; then
    echo "[update] restarting service"
    stop_bg
    start_bg
  else
    echo "[update] service not running, skip restart"
  fi

  echo "[update] updated: $(git -C "$REPO_ROOT" describe --tags --always --dirty)"
}

main() {
  local cmd="${1:-help}"
  case "$cmd" in
    install) install_deps ;;
    configure) configure_wizard ;;
    check) check_runtime ;;
    version) show_version ;;
    update-check) update_check ;;
    update) update_now ;;
    logs-clear) clear_logs ;;
    tg-user-id) get_telegram_user_id "${2:-}" ;;
    start) start_bg ;;
    run) run_fg ;;
    stop) stop_bg ;;
    restart) stop_bg; start_bg ;;
    status) status_bg ;;
    logs) tail_logs ;;
    install-start) install_deps; start_bg ;;
    help|-h|--help) usage ;;
    *)
      echo "[error] unknown command: $cmd" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
