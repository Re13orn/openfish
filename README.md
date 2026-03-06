<p align="center">
  <img src="docs/logo.png" alt="OpenFish Logo" width="220" />
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

![OpenFish Architecture](docs/ARCHITECTURE_EN.png)

Detailed architecture notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

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

## Quick Start

```bash
bash mvp_scaffold/scripts/install_start.sh install
bash mvp_scaffold/scripts/install_start.sh configure
bash mvp_scaffold/scripts/install_start.sh check
bash mvp_scaffold/scripts/install_start.sh start
```

If you do not know your Telegram user ID yet, send `/start` to the bot first, then run:

```bash
bash mvp_scaffold/scripts/install_start.sh tg-user-id
```

## Command Overview

Core commands:

- `/projects`, `/use <project>`, `/status`
- `/ask <question>`, `/do <task>`, `/resume [task_id] [instruction]`
- `/approve [note]`, `/reject [reason]`, `/cancel`
- `/diff`, `/memory`, `/note <text>`, `/help`

Extended commands:

- `/project-root [abs_path]`
- `/project-add`, `/project-disable`, `/project-archive`
- `/templates`, `/run`, `/skills`, `/skill-install`
- `/schedule-add`, `/schedule-list`, `/schedule-run`, `/schedule-pause`, `/schedule-enable`, `/schedule-del`
- `/start`, `/last`, `/retry`, `/upload_policy`

Telegram quick buttons cover all command capabilities:

- no-arg commands execute directly
- high-friction commands enter persisted step-by-step wizards

## Documentation

User-facing docs:

- Chinese homepage: [README_CN.md](README_CN.md)
- Architecture details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Persistence details: [docs/PERSISTENCE_ARCHITECTURE.md](docs/PERSISTENCE_ARCHITECTURE.md)
- Install/Deploy/Usage manual (Chinese): [docs/安装部署和使用手册.md](docs/安装部署和使用手册.md)

Internal docs:

- Product spec: [docs/internal/SPEC.md](docs/internal/SPEC.md)
- Agent design rules: [docs/internal/AGENTS.md](docs/internal/AGENTS.md)
- Product design story: [docs/internal/系统设计理念与开发历程.md](docs/internal/系统设计理念与开发历程.md)
- 5-minute pitch: [docs/internal/5分钟精简路演版.md](docs/internal/5分钟精简路演版.md)
- GitHub release checklist: [docs/internal/GitHub开源发布清单.md](docs/internal/GitHub开源发布清单.md)

## Repository Layout

- Runtime app: `mvp_scaffold/`
- Docs: `docs/`
- Internal docs: `docs/internal/`
- Config samples: `env.example`, `projects.example.yaml`
- DB schema: `schema.sql`

## Security Notes

- Rotate bot token immediately if it appears in logs/screenshots.
- Do not commit `.env`, runtime data, or local secret-bearing config.
- Keep allowed project directories minimal and explicit.
