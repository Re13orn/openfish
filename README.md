<p align="center">
  <img src="docs/openfish_trending_icon_animated.svg" alt="OpenFish Logo" width="220" />
</p>

<h1 align="center">OpenFish</h1>
<p align="center"><strong>Single-User, Telegram-Driven Local Codex Assistant</strong></p>

<p align="center">
  <a href="README_CN.md">中文版</a> |
  <a href="LICENSE">MIT License</a> |
  <a href="CONTRIBUTING.md">Contributing</a> |
  <a href="SECURITY.md">Security</a> |
  <a href="CHANGELOG.md">Changelog</a>
</p>

<p align="center">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-1f6feb" />
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white" />
  <img alt="PRs" src="https://img.shields.io/badge/PRs-welcome-2ea043" />
  <img alt="Architecture" src="https://img.shields.io/badge/Architecture-Single--Process-7a3cff" />
  <img alt="Powered by" src="https://img.shields.io/badge/Powered%20by-Codex%20CLI-d97706" />
</p>

OpenFish is a local-first remote coding assistant for one trusted owner.
It lets you control local repositories from Telegram while execution, state, approvals, and audit logs remain on your machine.

## Architecture

### Module View

```mermaid
flowchart LR
    U[Telegram User] --> TG[Telegram Bot API]
    TG --> A[telegram_adapter.py]
    A --> R[router.py]

    R --> PR[project_registry.py]
    R --> TS[task_store.py]
    R --> AU[audit.py]
    R --> AP[approval.py]
    R --> CR[codex_runner.py]
    R --> RI[repo_inspector.py]
    R --> SS[skills_service.py]
    R --> MS[mcp_service.py]

    CR --> CCLI[Codex CLI]
    CCLI --> REPO[Local Repositories]

    TS --> DB[(SQLite)]
    AU --> DB
    PR --> CFG[projects.yaml]

    SCH[scheduler.py] --> TS
    SCH --> R
```

### Runtime Flow

```mermaid
sequenceDiagram
    participant User as Telegram User
    participant Adapter as Telegram Adapter
    participant Router as Command Router
    participant Store as Task Store
    participant Codex as Codex Runner

    User->>Adapter: /ask or /do
    Adapter->>Router: CommandContext
    Router->>Store: create task + mark running
    Router->>Codex: execute request in active project
    Codex-->>Router: summary/session/exit_code
    Router->>Store: finalize task + update project state
    Router-->>Adapter: CommandResult
    Adapter-->>User: concise mobile reply
```

## Product Scope

OpenFish is built for:

- single-user operation
- project-scoped continuity
- conservative execution boundaries
- concise mobile-friendly Telegram interaction

OpenFish is not:

- a multi-user bot platform
- a public remote shell
- a cloud orchestration system

## Core Capabilities

- Project lifecycle: list, select, add, disable, archive
- Task lifecycle: ask, do, resume, retry, cancel
- Scheduling: add/list/run/pause/enable/delete periodic tasks
- Approval flow: approve/reject continuation
- Project memory: notes, recent summaries, status snapshots
- Safe file analysis: upload with extension/size/path checks

## Telegram UX

- High-frequency home keyboard for `Projects`, `Ask`, `Do`, `Status`, `Resume`, `Diff`, `Schedule`, `More`, `Help`
- Button-first approval flow, including note/reason wizards tied to explicit `approval_id`
- Persisted step-by-step wizards for project add, schedule add, template run, and approval note/reason
- Chat-scoped UI mode with concise mobile summaries
- Status/projects/schedule/approval/more panels now prefer updating the latest card instead of spamming new messages
- Short-window outbound dedup plus recent message reference tracking for stable Telegram rendering

## ><> CLI Quick Start

Install from PyPI:

```bash
pip install openfish
```

Then run OpenFish through the `><> openfish` CLI:

```bash
openfish install
openfish configure
openfish check
openfish start
```

`><>` Primary lifecycle commands:

- `openfish install`
- `openfish uninstall`
- `openfish configure`
- `openfish init-home`
- `openfish check`
- `openfish start`
- `openfish stop`
- `openfish restart`
- `openfish status`
- `openfish logs`

`><>` Update behavior is mode-aware:

- repository mode: `openfish update` performs git-based self-update
- package/home mode: use `python -m pip install --upgrade openfish`

`><>` If you want a user-home runtime instead of repository-local runtime data, bootstrap it first:

```bash
openfish init-home
export OPENFISH_HOME=~/.config/openfish
openfish check
openfish start
```

`><>` If you are developing from source and want editable installs instead:

```bash
pip install -e ./mvp_scaffold
```

`><>` Uninstall the package:

```bash
openfish uninstall
```

To remove runtime config and data at the same time:

```bash
openfish uninstall --purge-runtime
```

`><>` If you do not know your Telegram user ID yet, send `/start` to the bot first, then run:

```bash
openfish tg-user-id
```

`><>` Legacy script entrypoint remains available for compatibility:

```bash
bash mvp_scaffold/scripts/install_start.sh start
```

## ><> Docker

OpenFish also includes an isolated Docker runtime for long-running self-hosted deployment.

```bash
openfish docker-init
```

Current Docker mode is isolated from the host runtime layout:

- OpenFish home lives in a Docker volume at `/var/lib/openfish`
- the default project root is fixed inside the container at `/workspace/projects`
- Codex auth state lives in a Docker volume at `/root/.codex`
- runtime state, logs, SQLite, and `projects.yaml` live in named Docker volumes
- repository-local `.env`, `projects.yaml`, and `mvp_scaffold/data` are not mounted into the container

Docker is optional. For local owner-operated usage, the `openfish` CLI remains the primary path.

`><>` Supported Docker helper commands:

- `openfish docker-init`
- `openfish docker-configure`
- `openfish docker-up`
- `openfish docker-down`
- `openfish docker-health`
- `openfish docker-logs`
- `openfish docker-ps`
- `openfish docker-login-codex`
- `openfish docker-codex-status`

`><>` Recommended flow:

1. `openfish docker-init`
2. `openfish docker-login-codex`
3. `openfish docker-codex-status`

`openfish docker-init` is the shortest path. It will run `docker-configure` when needed, start the container, and then run `docker-health`.

If you need to log Codex into the container after it starts:

```bash
openfish docker-login-codex
openfish docker-codex-status
```

`openfish docker-configure` writes Docker-specific runtime settings into `.openfish.docker.env` and guides:

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_TELEGRAM_USER_IDS`
- optional `DEFAULT_PROJECT`
- optional bootstrap project key/name

`openfish docker-login-codex` supports:

- device auth login
- importing a local `auth.json` path such as `~/.codex/auth.json`
- pasting raw `auth.json` content directly

## ><> Command Overview

Core commands:

- `/projects`, `/use <project>`, `/status`
- `/ask <question>`, `/do <task>`, `/resume [task_id] [instruction]`
- `/approve [note]`, `/reject [reason]`, `/cancel`
- `/diff`, `/memory`, `/note <text>`, `/help`

Extended commands:

- `/project-root [abs_path]`
- `/project-add`, `/project-disable`, `/project-archive`
- `/skills`, `/skill-install`
- `/schedule-add`, `/schedule-list`, `/schedule-run`, `/schedule-pause`, `/schedule-enable`, `/schedule-del`
- `/start`, `/last`, `/retry`, `/upload_policy`

Telegram quick buttons cover all command capabilities:

- no-arg commands execute directly
- high-friction commands enter persisted step-by-step wizards

## Documentation

User-facing docs:

- Chinese homepage: [README_CN.md](README_CN.md)
- Persistence details: [docs/PERSISTENCE_ARCHITECTURE.md](docs/PERSISTENCE_ARCHITECTURE.md)
- Install/Deploy/Usage manual (Chinese): [docs/安装部署和使用手册.md](docs/安装部署和使用手册.md)

## Repository Layout

- Runtime app: `mvp_scaffold/`
- Docs: `docs/`
- Config samples: `env.example`, `projects.example.yaml`
- DB schema: `schema.sql`

## Security Notes

- Rotate bot token immediately if it appears in logs/screenshots.
- Do not commit `.env`, runtime data, or local secret-bearing config.
- Keep allowed project directories minimal and explicit.
