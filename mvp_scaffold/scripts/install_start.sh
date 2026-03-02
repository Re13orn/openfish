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

PYTHON_BIN="${PYTHON_BIN:-python3.12}"

usage() {
  cat <<'EOF'
Usage:
  bash mvp_scaffold/scripts/install_start.sh install
  bash mvp_scaffold/scripts/install_start.sh configure
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
  configure     Interactive wizard for .env and projects.yaml.
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
  local sqlite_path="$4"
  local migrations_dir="$5"
  local codex_bin="$6"
  local log_level="$7"
  local poll_interval="$8"
  local msg_len="$9"
  local timeout="${10}"
  local sandbox_mode="${11}"
  local approval_mode="${12}"
  local json_output="${13}"
  local enable_upload="${14}"
  local max_upload_size="${15}"
  local upload_temp_dir="${16}"
  local upload_extensions="${17}"

  cat > "$ENV_FILE" <<EOF
# Telegram
TELEGRAM_BOT_TOKEN=$token
ALLOWED_TELEGRAM_USER_IDS=$allowed_ids
TELEGRAM_POLL_INTERVAL_SECONDS=$poll_interval

# App paths
PROJECTS_CONFIG_PATH=$projects_path
SQLITE_PATH=$sqlite_path
MIGRATIONS_DIR=$migrations_dir
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

configure_wizard() {
  if [[ ! -t 0 ]]; then
    echo "[warn] configure 需要交互终端，当前为非交互环境。"
    return
  fi

  load_env
  echo "[configure] 开始交互式配置向导"

  local token_default="${TELEGRAM_BOT_TOKEN:-}"
  if [[ "$token_default" == "your_telegram_bot_token_here" ]]; then
    token_default=""
  fi
  local ids_default="${ALLOWED_TELEGRAM_USER_IDS:-}"
  if ! is_valid_user_ids "$ids_default"; then
    ids_default=""
  fi
  local projects_default="${PROJECTS_CONFIG_PATH:-./mvp_scaffold/projects.yaml}"
  local sqlite_default="${SQLITE_PATH:-./mvp_scaffold/data/app.db}"
  local migrations_default="${MIGRATIONS_DIR:-./mvp_scaffold/migrations}"
  if [[ "$projects_default" == "./projects.yaml" ]]; then
    projects_default="./mvp_scaffold/projects.yaml"
  fi
  if [[ "$sqlite_default" == "./data/app.db" ]]; then
    sqlite_default="./mvp_scaffold/data/app.db"
  fi
  if [[ "$migrations_default" == "./migrations" ]]; then
    migrations_default="./mvp_scaffold/migrations"
  fi
  local codex_default="${CODEX_BIN:-codex}"
  local log_default="${LOG_LEVEL:-INFO}"
  local poll_default="${TELEGRAM_POLL_INTERVAL_SECONDS:-2}"
  local msg_len_default="${MAX_TELEGRAM_MESSAGE_LENGTH:-3500}"
  local timeout_default="${CODEX_COMMAND_TIMEOUT_SECONDS:-1800}"
  local sandbox_default="${CODEX_DEFAULT_SANDBOX_MODE:-workspace-write}"
  local approval_default="${CODEX_DEFAULT_APPROVAL_MODE:-on-request}"
  local json_default="${CODEX_JSON_OUTPUT:-true}"
  local upload_enable_default="${ENABLE_DOCUMENT_UPLOAD:-true}"
  local upload_size_default="${MAX_UPLOAD_SIZE_BYTES:-209715200}"
  local upload_temp_dir_default="${UPLOAD_TEMP_DIR_NAME:-.codex_telegram_uploads}"
  local upload_ext_default="${ALLOWED_UPLOAD_EXTENSIONS:-txt,md,markdown,json,yaml,yml,xml,csv,log,ini,toml,py,js,ts,tsx,jsx,go,rs,java,kt,swift,sql,html,css,apk}"

  local token ids projects_path sqlite_path migrations_dir codex_bin
  local log_level poll_interval msg_len timeout sandbox_mode approval_mode json_output
  local enable_upload max_upload_size upload_temp_dir upload_extensions
  prompt_secret token "请输入 TELEGRAM_BOT_TOKEN" "$token_default" 1
  while true; do
    prompt_value ids "请输入 ALLOWED_TELEGRAM_USER_IDS（逗号分隔）" "$ids_default" 1
    if is_valid_user_ids "$ids"; then
      break
    fi
    echo "[warn] 必须是纯数字 Telegram 用户 ID，多个请用逗号分隔。"
    ids_default=""
  done
  prompt_value projects_path "PROJECTS_CONFIG_PATH" "$projects_default" 1
  prompt_value sqlite_path "SQLITE_PATH" "$sqlite_default" 1
  prompt_value migrations_dir "MIGRATIONS_DIR" "$migrations_default" 1
  prompt_value codex_bin "CODEX_BIN" "$codex_default" 1
  prompt_value log_level "LOG_LEVEL" "$log_default" 1
  prompt_value poll_interval "TELEGRAM_POLL_INTERVAL_SECONDS" "$poll_default" 1
  prompt_value msg_len "MAX_TELEGRAM_MESSAGE_LENGTH" "$msg_len_default" 1
  prompt_value timeout "CODEX_COMMAND_TIMEOUT_SECONDS" "$timeout_default" 1
  prompt_value sandbox_mode "CODEX_DEFAULT_SANDBOX_MODE" "$sandbox_default" 1
  prompt_value approval_mode "CODEX_DEFAULT_APPROVAL_MODE" "$approval_default" 1
  prompt_value json_output "CODEX_JSON_OUTPUT(true/false)" "$json_default" 1
  prompt_value enable_upload "ENABLE_DOCUMENT_UPLOAD(true/false)" "$upload_enable_default" 1
  prompt_value max_upload_size "MAX_UPLOAD_SIZE_BYTES" "$upload_size_default" 1
  prompt_value upload_temp_dir "UPLOAD_TEMP_DIR_NAME" "$upload_temp_dir_default" 1
  prompt_value upload_extensions "ALLOWED_UPLOAD_EXTENSIONS(逗号分隔)" "$upload_ext_default" 1

  write_env_file \
    "$token" "$ids" "$projects_path" "$sqlite_path" "$migrations_dir" "$codex_bin" \
    "$log_level" "$poll_interval" "$msg_len" "$timeout" "$sandbox_mode" "$approval_mode" "$json_output" \
    "$enable_upload" "$max_upload_size" "$upload_temp_dir" "$upload_extensions"
  echo "[configure] 已写入 ${ENV_FILE}"

  local should_write_project="y"
  if [[ -f "$PROJECTS_FILE" ]]; then
    if prompt_yes_no "检测到 ${PROJECTS_FILE}，是否覆盖为向导生成内容？" "n"; then
      should_write_project="y"
    else
      should_write_project="n"
    fi
  fi

  if [[ "$should_write_project" == "y" ]]; then
    local project_key project_name project_path default_branch test_command allowed_dirs
    prompt_value project_key "项目 key（用于 /use）" "demo" 1
    prompt_value project_name "项目显示名" "$project_key" 1
    prompt_value project_path "项目本地路径（建议绝对路径）" "$HOME/work/$project_key" 1
    prompt_value default_branch "默认分支" "main" 1
    prompt_value test_command "测试命令" "pytest -q" 1
    prompt_value allowed_dirs "allowed_directories（逗号分隔）" "$project_path" 1

    local allowed_yaml=""
    local item=""
    local old_ifs="$IFS"
    IFS=','
    for item in $allowed_dirs; do
      item="${item#"${item%%[![:space:]]*}"}"
      item="${item%"${item##*[![:space:]]}"}"
      [[ -z "$item" ]] && continue
      allowed_yaml="${allowed_yaml}"$'\n'"      - $(yaml_quote "$item")"
    done
    IFS="$old_ifs"
    if [[ -z "$allowed_yaml" ]]; then
      allowed_yaml=$'\n'"      - $(yaml_quote "$project_path")"
    fi

    cat > "$PROJECTS_FILE" <<EOF
projects:
  $project_key:
    name: $(yaml_quote "$project_name")
    path: $(yaml_quote "$project_path")
    default_branch: $(yaml_quote "$default_branch")
    test_command: $(yaml_quote "$test_command")
    allowed_directories:${allowed_yaml}
EOF
    echo "[configure] 已写入 ${PROJECTS_FILE}"
  fi

  echo "[configure] 配置完成。你可以执行："
  echo "  bash mvp_scaffold/scripts/install_start.sh start"
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
[next] Please edit $ENV_FILE before start:
  - TELEGRAM_BOT_TOKEN
  - ALLOWED_TELEGRAM_USER_IDS
  - PROJECTS_CONFIG_PATH=./mvp_scaffold/projects.yaml
  - SQLITE_PATH=./mvp_scaffold/data/app.db
  - MIGRATIONS_DIR=./mvp_scaffold/migrations
EOF
    else
      echo "[warn] env.example not found, please create $ENV_FILE manually"
    fi
  fi

  if [[ "${SKIP_CONFIG_WIZARD:-0}" != "1" && -t 0 ]]; then
    if prompt_yes_no "是否立即运行配置向导（推荐）？" "y"; then
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

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

start_bg() {
  validate_runtime_config
  prepare_dirs

  if is_running; then
    echo "[start] already running (pid=$(cat "$PID_FILE"))"
    return
  fi

  echo "[start] launching in background"
  (
    cd "$APP_DIR"
    nohup "$VENV_DIR/bin/python" -m src.main >>"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
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
  if ! is_running; then
    echo "[stop] not running"
    rm -f "$PID_FILE"
    return
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  echo "[stop] stopping pid=$pid"
  kill "$pid"

  for _ in {1..20}; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      sleep 0.2
    else
      rm -f "$PID_FILE"
      echo "[stop] stopped"
      return
    fi
  done

  echo "[stop] graceful stop timeout, sending SIGKILL"
  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$PID_FILE"
}

status_bg() {
  if is_running; then
    echo "running (pid=$(cat "$PID_FILE"))"
    echo "log: $LOG_FILE"
  else
    echo "stopped"
  fi
}

tail_logs() {
  prepare_dirs
  touch "$LOG_FILE"
  tail -f "$LOG_FILE"
}

main() {
  local cmd="${1:-help}"
  case "$cmd" in
    install) install_deps ;;
    configure) configure_wizard ;;
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
