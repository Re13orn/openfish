# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

## [Unreleased]

- No unreleased changes yet.

## [1.0.0] - 2026-03-08

### Added

- Unified Codex session browser that combines:
  - OpenFish-tracked task sessions
  - local native Codex sessions discovered from `~/.codex`
- Session detail and import flow:
  - `/sessions [page]`
  - `/session <id>`
  - `/session-import <id> [project_key] [name]`
- Telegram service controls:
  - `/restart`
  - `/logs`
  - `/logs-clear`
- Self-update controls:
  - `/version`
  - `/update-check`
  - `/update`
- Telegram “更多” panel entries for:
  - version
  - update check
  - update
  - restart
  - logs
  - log clearing

### Changed

- Default Telegram UI mode is now `stream` for new chats unless overridden by `DEFAULT_UI_MODE`.
- `/ask` and `/do` in the same active project now automatically continue the most recent Codex session instead of silently starting a fresh context every time.
- Telegram runtime now uses separate connection-pool settings for:
  - update polling
  - outbound message delivery
- Native Codex sessions can now be adopted into OpenFish by creating or reusing a project and binding the session for future continuation.

### Fixed

- Telegram stalls where the process stayed alive but polling stopped responding because the shared HTTP pool was exhausted.
- Conversation continuity gaps where a follow-up `/ask`, `/do`, or plain-text message lost the previous Codex context.
- Missing operational controls in Telegram that previously required local shell access for restart/log/update actions.

## [1.0.0-rc1] - 2026-03-07

### Added

- Release-candidate publication track for the first stable major version line.
- `v1.0` readiness documentation:
  - `docs/internal/v1.0发布准备清单.md`
  - `docs/internal/SMOKE_TEST_CHECKLIST.md`
- Dedicated `v1.0.0-rc1` release notes artifact in `docs/releases/v1.0.0-rc1.md`.

### Changed

- Clarified release process and maintenance expectations for the `v1.x` line.
- Cleaned `CHANGELOG.md` so `Unreleased` is no longer carrying older release content.
- Updated runtime path resolution so relative values from `.env` are interpreted consistently against the repository/app layout instead of the current shell working directory.
- Hardened background startup flow in `install_start.sh` by detaching stdin and disowning the child process before recording PID state.

### Fixed

- Runtime startup failures caused by `.env` relative paths resolving to the wrong SQLite/projects/migrations locations.
- False service-start success followed by immediate background-process exit in some shell launch contexts.

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
