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
