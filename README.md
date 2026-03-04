<p align="center">
  <img src="docs/logo.png" alt="OpenFish Logo" width="220" />
</p>

<h1 align="center">OpenFish</h1>
<p align="center"><strong>Single-User, Telegram-Driven Local Codex Assistant</strong></p>

<p align="center">
  <a href="README_CN.md">Chinese</a> |
  <a href="LICENSE">MIT License</a> |
  <a href="CONTRIBUTING.md">Contributing</a> |
  <a href="CHANGELOG.md">Changelog</a>
</p>

OpenFish is a local-first remote coding assistant for one trusted owner.
You control local projects from Telegram, while execution, state, approvals, and audit logs stay on your machine.

## What It Is

- Single-user only (allowlisted Telegram account)
- Project-centric continuity (state persists per project)
- Local execution with Codex CLI
- Conservative by default (path guard + approval flow)
- Mobile-friendly summaries for Telegram

## What It Is Not

- Multi-user bot platform
- Public remote shell
- Cloud orchestration framework
- Plugin marketplace

## Core Capabilities

- Project lifecycle: list, select, add, disable, archive
- Task execution: ask, do, resume, retry, cancel
- Scheduling: create/list/run/pause/enable/delete periodic tasks
- Memory and notes: per-project summaries and notes
- Approval workflow: approve/reject risky continuation
- Diff/status visibility: quick mobile-friendly task and repo status
- Document upload analysis with extension/size/path checks

## Quick Start

```bash
# from repository root
cd mvp_scaffold
bash scripts/install_start.sh
```

Then follow the interactive setup (`configure`) to generate `.env` and `projects.yaml`, and start the service.

## Command Reference

Core commands:

- `/projects`, `/use <project>`, `/status`
- `/ask <question>`, `/do <task>`, `/resume [task_id] [instruction]`
- `/approve [note]`, `/reject [reason]`, `/cancel`
- `/diff`, `/memory`, `/note <text>`, `/help`

Extended commands:

- Project lifecycle: `/project-add`, `/project-disable`, `/project-archive`
- Templates/skills: `/templates`, `/run`, `/skills`, `/skill-install`
- Scheduling: `/schedule-add`, `/schedule-list`, `/schedule-run`, `/schedule-pause`, `/schedule-enable`, `/schedule-del`
- Utility: `/start`, `/last`, `/retry`, `/upload_policy`

Telegram quick buttons cover all command capabilities:

- No-arg commands run directly by tap
- Param commands enter guided input mode and apply command prefix to the next message

## Documentation Map

- Chinese install/deploy/usage manual: [docs/安装部署和使用手册.md](docs/安装部署和使用手册.md)
- Chinese product design story: [docs/系统设计理念与开发历程.md](docs/系统设计理念与开发历程.md)
- Chinese 5-minute pitch: [docs/5分钟精简路演版.md](docs/5分钟精简路演版.md)
- GitHub release checklist (Chinese): [docs/GitHub开源发布清单.md](docs/GitHub开源发布清单.md)
- Full product spec: [SPEC.md](SPEC.md)
- Agent constraints and design rules: [AGENTS.md](AGENTS.md)

## Architecture (High-Level)

```text
Telegram Bot API
    -> telegram_adapter
    -> command_router
       -> project_registry (YAML)
       -> task_store/state (SQLite)
       -> approval_service
       -> codex_runner
```

## Security Notes

- Rotate bot token immediately if exposed in logs or screenshots.
- Do not commit `.env`, runtime data, or local project config containing secrets.
- Keep project paths explicit and limited to trusted directories.

## Repository Structure

- Runtime app: `mvp_scaffold/`
- Docs and collateral: `docs/`
- Config samples: `env.example`, `projects.example.yaml`
- DB schema: `schema.sql`
