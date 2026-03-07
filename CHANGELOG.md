# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

## [Unreleased]

### Added

- Minimal periodic task scheduling:
  - `/schedule-add <HH:MM> <ask|do> <text>`
  - `/schedule-list`
  - `/schedule-run <id>`
  - `/schedule-pause <id>`
  - `/schedule-enable <id>`
  - `/schedule-del <id>`
- Local background scheduler service that polls due tasks and triggers Codex runs.
- Missed-run policy support for scheduler (`skip` / `catchup_once`).
- SQLite migration `0003_scheduled_tasks.sql` for durable scheduled task state and last-run results.
- `/resume [task_id] [instruction]` now supports task-scoped resume with session-aware fallback.
- Telegram polling now auto-recovers from long network outages with indefinite exponential backoff retry.
- Project lifecycle commands:
  - `/project-add <key> [abs_path] [name]`
  - `/project-disable <key>`
  - `/project-archive <key>`
- Project creation defaults:
  - `/project-root [abs_path]` to view/set the default project root directory.
  - `/project-add <key> [abs_path] [name]` now supports omitting path and auto-creates project directory under default root.
- Telegram 按钮面板增强：
  - 主菜单新增工具/帮助入口，分层面板覆盖全部命令能力。
  - 参数命令支持“输入引导”模式，点击后下一条消息自动按对应命令执行。

## [0.9.0] - 2026-03-07

### Added

- Telegram-first control surface with high-frequency home keyboard, layered panels, and button-first interaction.
- Persisted step-by-step wizards for project creation, schedule creation, template execution, and approval note/reason input.
- Chat-scoped UI modes:
  - `/ui summary`
  - `/ui verbose`
  - `/ui stream`
- Chat-scoped Codex model selection:
  - `/model`
  - `/model set <name>`
  - `/model reset`
- MCP inspection and management from Telegram:
  - `/mcp`
  - `/mcp <name>`
  - `/mcp-enable <name>`
  - `/mcp-disable <name>`
- Controlled editing of `~/.codex/config.toml` for MCP enable/disable without exposing arbitrary config editing.
- Telegram stream/progress delivery improvements with typing indicators and streamed progress card updates.
- Long-result delivery splitting for Telegram so large final conclusions can be delivered across multiple messages.
- Project memory pagination:
  - `/memory [page]`
  - Telegram previous/next page navigation
- Single-instance process locking and stale-lock recovery for safer long-running local service operation.
- Focused persistence store split:
  - `chat_state_store`
  - `approval_store`
  - `schedule_store`
  - `project_state_store`
  - `task_runtime_store`
- Persistence architecture documentation in `docs/PERSISTENCE_ARCHITECTURE.md`.

### Changed

- Reworked Telegram UX around high-frequency daily use on mobile instead of command memorization.
- `/status` evolved into a clearer control card with state-aware actions and panel shortcuts.
- Approval flow now binds callbacks to explicit `approval_id` and supports safer note/reason follow-up flows.
- Telegram panels now prefer updating the latest relevant card instead of spamming new messages:
  - status
  - projects
  - schedule list
  - approval panel
  - more panel
- Outbound Telegram delivery now tracks recent message references and short-window dedup state for more stable rendering.
- Project memory display now preserves full note/task content and supports page-based browsing.
- First-run bootstrap was simplified into:
  - `install`
  - `configure`
  - `check`
  - `start`
- Install and usage documentation was rewritten to match the current Telegram interaction model.

### Fixed

- Scheduler SQLite thread-affinity failures caused by cross-thread connection usage.
- Recovery of stuck historical tasks on restart so old `created`/`running` tasks no longer remain orphaned forever.
- Telegram bootstrap behavior during network loss now retries indefinitely instead of aborting early.
- Oversized Telegram document uploads now return a user-facing explanation instead of surfacing as an unhandled exception.
- `.zip` document uploads are now allowed by default.
- Project disable/archive now clears project session state so reactivation behaves predictably.
- `install_start.sh start` no longer exits early when the lock file is absent.
- Plain text Telegram questions now correctly follow the `/ask` path, including typing and stream behavior.
- Telegram memory output and final task results no longer truncate as aggressively as earlier versions.

## [0.1.0] - 2026-03-02

### Added

- Core Telegram long-polling service with allowlisted user authentication.
- Project registry loading from YAML and SQLite durable state initialization.
- Command flow for `/projects`, `/use`, `/ask`, `/do`, `/status`, `/help`.
- Continuity and control commands including `/resume`, `/approve`, `/reject`, `/cancel`, `/diff`, `/memory`, `/note`.
- Usability commands `/start`, `/templates`, `/run`, `/last`, `/retry`.
- Safe document upload analysis path with extension whitelist and size limits (default 200MB).
- Basic audit/event logging and local CI script.
- Installation/start helper script with interactive configure wizard and Telegram user ID helper.

### Changed

- Rebranded product name to **OpenFish**.
- Improved Telegram reply reliability with transient network timeout retries and safer error handling.
