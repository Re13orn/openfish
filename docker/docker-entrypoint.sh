#!/usr/bin/env bash
set -euo pipefail

export OPENFISH_HOME="${OPENFISH_HOME:-/var/lib/openfish}"
export OPENFISH_DOCKER_MODE="${OPENFISH_DOCKER_MODE:-1}"
export DEFAULT_PROJECT_ROOT="${DEFAULT_PROJECT_ROOT:-/workspace/projects}"
export CODEX_HOME="${CODEX_HOME:-/root/.codex}"
OPENFISH_BIN="${OPENFISH_BIN:-/app/mvp_scaffold/.venv/bin/openfish}"

mkdir -p "$OPENFISH_HOME" "$DEFAULT_PROJECT_ROOT" "$CODEX_HOME"

"$OPENFISH_BIN" install >/dev/null

python - <<'PY'
import os
from pathlib import Path
import yaml

home = Path(os.environ["OPENFISH_HOME"])
env_path = home / ".env"
projects_path = home / "projects.yaml"

content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""

def replace_key(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    replaced = False
    for idx, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[idx] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    return "\n".join(lines).rstrip("\n") + "\n"

for key, value in (
    ("DEFAULT_PROJECT_ROOT", os.environ.get("DEFAULT_PROJECT_ROOT", "/workspace/projects")),
    ("CODEX_HOME", os.environ.get("CODEX_HOME", "/root/.codex")),
):
    content = replace_key(content, key, value)

for key in ("TELEGRAM_BOT_TOKEN", "ALLOWED_TELEGRAM_USER_IDS", "DEFAULT_PROJECT"):
    value = os.environ.get(key, "").strip()
    if value:
        content = replace_key(content, key, value)

env_path.write_text(content, encoding="utf-8")

project_key = os.environ.get("OPENFISH_BOOTSTRAP_PROJECT_KEY", "").strip()
if project_key:
    project_name = os.environ.get("OPENFISH_BOOTSTRAP_PROJECT_NAME", project_key).strip() or project_key
    project_path = Path(os.environ.get("DEFAULT_PROJECT_ROOT", "/workspace/projects")) / project_key
    project_path.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "default_project_root": str(Path(os.environ.get("DEFAULT_PROJECT_ROOT", "/workspace/projects"))), "projects": {}}
    if projects_path.exists():
        loaded = yaml.safe_load(projects_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data.update({k: v for k, v in loaded.items() if k != "projects"})
            if isinstance(loaded.get("projects"), dict):
                data["projects"] = loaded["projects"]
    data["projects"][project_key] = {
        "name": project_name,
        "path": str(project_path),
        "allowed_directories": [str(project_path)],
    }
    projects_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
PY

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${ALLOWED_TELEGRAM_USER_IDS:-}" ]]; then
  echo "[docker] 缺少必要环境变量。" >&2
  echo "[docker] 请至少提供 TELEGRAM_BOT_TOKEN 和 ALLOWED_TELEGRAM_USER_IDS。" >&2
  echo "[docker] 如需预置项目，可额外提供 OPENFISH_BOOTSTRAP_PROJECT_KEY。" >&2
  exit 1
fi

exec "$OPENFISH_BIN" run
