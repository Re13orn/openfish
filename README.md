<p align="center">
  <img src="docs/logo.png" alt="OpenFish Logo" width="220" />
</p>

<h1 align="center">OpenFish</h1>
<p align="center"><strong>Single-User, Telegram-Driven Local Codex Assistant</strong></p>

<p align="center">
  <a href="LICENSE">MIT License</a> |
  <a href="CONTRIBUTING.md">Contributing</a> |
  <a href="CHANGELOG.md">Changelog</a>
</p>

<p align="center">
  Project-Centric Continuity | Conservative by Default | Mobile-Friendly Telegram UX
</p>

<p align="center">
  <a href="#what-it-does">Quick Start</a> |
  <a href="#core-ideas">Core Ideas</a> |
  <a href="#suggested-architecture">Architecture</a> |
  <a href="#current-command-set">Commands</a>
</p>

OpenFish is a practical remote coding assistant for one technical owner.
It runs on your own machine, uses Telegram as the control surface, and executes Codex strictly within registered local projects.

The product focus is intentionally narrow:
- **single user**,
- **local-first**,
- **project-scoped continuity**,
- **Codex CLI as execution engine**,
- **SQLite + YAML durable state**.

It is **not** a multi-user bot platform, cloud-hosted agent runtime, or general-purpose remote shell.

## Chinese manual

- Install/deploy/use guide (zh-CN): [docs/安装部署和使用手册.md](docs/安装部署和使用手册.md)
- Product intro speech (zh-CN): [docs/系统设计理念与开发历程.md](docs/系统设计理念与开发历程.md)
- 5-minute pitch (zh-CN): [docs/5分钟精简路演版.md](docs/5分钟精简路演版.md)
- GitHub release checklist (zh-CN): [docs/GitHub开源发布清单.md](docs/GitHub开源发布清单.md)
- Install/start script: `mvp_scaffold/scripts/install_start.sh`
  - Supports interactive `configure` wizard for `.env` and `projects.yaml`.
  - Supports `tg-user-id` helper to discover numeric Telegram user id.

## Open Source

- License: [MIT](LICENSE)
- Contributing guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security policy: [SECURITY.md](SECURITY.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)

Before publishing your own fork, rotate any exposed bot token and confirm `.env` / runtime data are not committed.

## What it does

From Telegram, you can:
- choose a registered project,
- ask questions about that project,
- directly send plain text (auto-routed as `/ask` after project selection),
- start a Codex task,
- upload supported documents for safe analysis in active project temp dir,
- receive short phase-style progress acknowledgements,
- inspect project and task status,
- review recent changes,
- approve or reject risky actions,
- and resume interrupted work later.

The system should preserve continuity through structured project memory and task state instead of relying only on raw chat history.

## Core ideas

### Project-scoped continuity
State is organized around a registered project, not just a Telegram chat. Each project can retain:
- local path,
- default branch,
- common commands,
- recent summaries,
- last Codex session id,
- known issues and notes.

### Conservative execution
The assistant should only operate inside approved directories for the selected project. High-risk actions should require explicit approval.

### Mobile-friendly responses
Telegram messages should be short, readable, and status-oriented. Long logs should be summarized.

### Auditable behavior
Important transitions should be recorded so you can understand what happened after the fact.

## Current command set

Supported commands:
- `/start`
- `/projects`
- `/project-add <key> <abs_path> [name]`
- `/project-disable <key>`
- `/project-archive <key>`
- `/use <project>`
- `/ask <question>`
- `/do <task>`
- `/templates`
- `/run <template> [extra]`
- `/skills`
- `/skill-install <source>`
- `/schedule-add <HH:MM> <ask|do> <text>`
- `/schedule-list`
- `/schedule-run <id>`
- `/schedule-pause <id>`
- `/schedule-enable <id>`
- `/schedule-del <id>`
- `/last`
- `/retry [extra]`
- `/status`
- `/resume [task_id] [instruction]`
- `/approve`
- `/reject`
- `/upload_policy`
- `/diff`
- `/memory`
- `/note <text>`
- `/cancel`
- `/help`

Telegram 快捷按钮已覆盖全部功能命令：
- 无参命令可直接点击执行（如 `/status`、`/projects`、`/memory`、`/skills`）。
- 需参数命令会进入“输入引导”模式，下一条消息自动补全前缀（如 `/note`、`/run`、`/schedule-add`、`/project-add`、`/skill-install`）。

## Suggested architecture

A small single-process service is preferred.

```text
Telegram Bot API
    ↓
Telegram Adapter
    ↓
Command Router
    ├─ Auth Guard
    ├─ Project Registry
    ├─ Task Store
    ├─ Project Memory Store
    ├─ Approval Gate
    ├─ Scheduled Task Service
    └─ Codex Runner
             ↓
         Codex CLI
             ↓
     Local Project Directory
```

## Repository layout

Recommended starting layout:

```text
.
├─ README.md
├─ SPEC.md
├─ AGENTS.md
├─ schema.sql
├─ projects.example.yaml
├─ .env.example
├─ src/
│  ├─ main.py
│  ├─ config.py
│  ├─ telegram_adapter.py
│  ├─ router.py
│  ├─ auth.py
│  ├─ project_registry.py
│  ├─ codex_runner.py
│  ├─ task_store.py
│  ├─ memory_store.py
│  ├─ approval.py
│  ├─ formatters.py
│  └─ models.py
├─ data/
│  ├─ app.db
│  ├─ summaries/
│  ├─ logs/
│  └─ artifacts/
└─ tests/
```

## Configuration

Two configuration layers are recommended:

1. **Environment variables** for secrets and process-level settings.
2. **YAML project registry** for allowed local projects.

### Suggested environment variables

```env
TELEGRAM_BOT_TOKEN=your_bot_token
ALLOWED_TELEGRAM_USER_IDS=123456789
TELEGRAM_RECONNECT_INITIAL_DELAY_SECONDS=2
TELEGRAM_RECONNECT_MAX_DELAY_SECONDS=300
TELEGRAM_RECONNECT_JITTER_SECONDS=1
PROJECTS_CONFIG_PATH=./projects.yaml
SQLITE_PATH=./data/app.db
MIGRATIONS_DIR=./migrations
LOG_LEVEL=INFO
DEFAULT_REPLY_LANGUAGE=zh-CN
DEFAULT_RISK_MODE=conservative
CODEX_BIN=codex
ENABLE_SCHEDULER=true
SCHEDULE_POLL_INTERVAL_SECONDS=20
SCHEDULE_MISSED_RUN_POLICY=skip
```

### Project registry

Start from `projects.example.yaml` and create your own `projects.yaml`.

Each project should declare:
- unique project key,
- local path,
- default branch,
- test command,
- optional dev command,
- allowed directories,
- optional description and notes,
- optional stack metadata.

## Database

Use SQLite for durable state. Initialize with:

```bash
sqlite3 data/app.db < schema.sql
```

Optional versioned migrations can be placed in `migrations/` with filenames like:

```text
0002_add_chat_context.sql
0003_some_change.sql
```

They are applied automatically on startup and recorded in `schema_migrations`.

The schema is designed to support:
- project registration metadata mirror,
- chat-level active project continuity,
- task history,
- task events,
- approvals,
- project state,
- memory notes,
- audit logs.

## Recommended development order

### Phase 1
Build the minimum working path:
- long polling Telegram bot,
- user allowlist check,
- `/use`, `/do`, `/status`,
- invoke Codex CLI in a selected project,
- persist basic task records in SQLite,
- return concise summaries to Telegram.

### Phase 2
Add continuity:
- `/resume`, `/diff`, `/memory`, `/note`,
- project state model,
- last Codex session tracking,
- task event logging,
- better formatting and progress updates.

### Phase 3
Add safer control and richer memory:
- approval gate,
- explicit pending actions,
- project summaries,
- recent task rollups,
- audit log improvements,
- light migration support.

## Security model

This project should follow these rules:
- only configured Telegram user ids may use the bot,
- every task must be tied to a registered project,
- execution must remain inside approved directories,
- risky operations should require approval,
- logs and chat output should avoid leaking secrets,
- state transitions should be recorded.

## Running locally

A minimal local workflow can look like this:

```bash
cp projects.example.yaml projects.yaml
cp .env.example .env
mkdir -p data/summaries data/logs data/artifacts
sqlite3 data/app.db < schema.sql
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

Adjust commands to match your actual implementation.

### Optional consistency check

You can run a static event-code consistency check:

```bash
python scripts/check_event_consistency.py
```

### Local CI (recommended)

Run all local quality checks in one command:

```bash
bash scripts/ci_local.sh
```

Or use Make:

```bash
make ci-local
```

## Relationship to SPEC.md and AGENTS.md

- `SPEC.md` defines the product and implementation requirements.
- `AGENTS.md` defines persistent working rules for Codex.
- `README.md` is the human-oriented quick start and project overview.

If implementation details conflict, follow this order:
1. explicit user instruction,
2. `SPEC.md`,
3. `AGENTS.md`,
4. `README.md`.

## Notes for Codex

When implementing this project:
- keep it single-process,
- keep dependencies light,
- do not add multi-user support,
- avoid premature abstractions,
- optimize for maintainability,
- and prefer clear, inspectable local state.
