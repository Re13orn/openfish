# SPEC.md

## Project Name

Personal Telegram Codex Assistant

---

## 1. Purpose

Build a single-user, Telegram-driven personal coding assistant that runs on the user's own computer and uses Codex CLI as its execution engine.

The system exists to solve one practical problem:

- the user already works locally with Codex on their computer,
- but when away from the computer, the user wants to continue interacting with projects remotely from a phone,
- especially through Telegram,
- while preserving project context, task continuity, safety boundaries, and auditability.

This is not a general agent platform. It is a local-first personal remote coding assistant.

---

## 2. Product Goal

The assistant must allow the user to:

- message a Telegram bot from a phone,
- select one of several registered local projects,
- ask read-oriented questions about a project,
- instruct Codex CLI to analyze or modify code inside that project,
- receive progress updates and concise result summaries in Telegram,
- inspect project state and recent changes,
- approve or reject risky actions,
- and resume interrupted work without restating all context.

The design should preserve continuity primarily through structured state and summaries, not by replaying raw chat history.

---

## 3. Non-Goals

The following are explicitly out of scope for the current version:

- multi-user support,
- group chat support,
- public bot access,
- web frontend or dashboard,
- cloud deployment,
- Slack / Discord / email channels,
- plugin marketplace,
- generic skills framework,
- distributed worker architecture,
- autonomous browsing agents,
- complex cost accounting,
- heavy terminal emulation inside Telegram,
- file upload and archive extraction workflows,
- webhook-based production hosting as the default mode.

If an implementation direction increases complexity without improving the core single-user Telegram-to-Codex workflow, reject it.

---

## 4. Primary User

There is exactly one intended user: the machine owner.

The owner:

- already uses Codex locally,
- has multiple local code projects,
- wants remote access through Telegram,
- and values continuity, safety, and simplicity over platform breadth.

---

## 5. Product Principles

### 5.1 Single-user first
Everything should be optimized for one trusted owner.

### 5.2 Local-first
Execution, state, project access, and memory remain on the user's computer.

### 5.3 Project-centric continuity
Continuity must be anchored to projects, not only to Telegram chats.

### 5.4 Conservative by default
The system should prefer read-only or explanatory behavior unless the user explicitly asks for action.

### 5.5 Understandable architecture
The codebase should stay small, direct, and easy for one technical owner to understand.

### 5.6 Mobile-friendly interaction
Telegram responses should be concise, structured, and useful on a phone.

### 5.7 Auditable behavior
Important actions should be persisted so the owner can understand what happened later.

---

## 6. Core User Stories

### Story 1: Select a project
As the user, I want to select a registered project so that subsequent requests target the correct repository.

Example:
- `/use myapp`

Expected behavior:
- the assistant validates that `myapp` exists in the project registry,
- sets it as the active project,
- and confirms the selected project and path.

### Story 2: Ask a project question
As the user, I want to ask questions about a project without necessarily changing code.

Examples:
- `/ask 这个项目的登录流程是什么？`
- `/ask 最近认证相关改动的风险点是什么？`

Expected behavior:
- the assistant loads project memory and current project state,
- optionally invokes Codex in a conservative read-oriented mode,
- and returns a concise mobile-friendly answer.

### Story 3: Start a coding task
As the user, I want to remotely ask the assistant to analyze or modify code through Codex.

Example:
- `/do 帮我定位登录接口 500 的原因并给出最小修复方案`

Expected behavior:
- a new task is created,
- the task is bound to the active project,
- Codex runs in the correct working directory,
- the system records progress and structured state,
- and the user receives updates and a final summary.

### Story 4: Inspect current state
As the user, I want to check the current project and task state.

Example:
- `/status`

Expected behavior:
- the assistant returns:
  - active project,
  - current branch if available,
  - working tree cleanliness if available,
  - last task summary,
  - pending approval status,
  - last known test result,
  - and recommended next step.

### Story 5: Resume prior work
As the user, I want to continue interrupted work without re-explaining everything.

Example:
- `/resume`

Expected behavior:
- the assistant finds the most recent resumable task for the active project,
- reconnects to the saved Codex session if possible,
- and continues from the saved state.

### Story 6: Approve or reject risky work
As the user, I want explicit approval checkpoints for risky actions.

Examples:
- `/approve`
- `/reject`

Expected behavior:
- if a task is blocked on approval, the task continues or is rejected,
- and the decision is recorded in the audit log.

### Story 7: Inspect recent changes
As the user, I want a concise explanation of recent modifications.

Example:
- `/diff`

Expected behavior:
- the assistant returns a compact summary of modified files and key change intent,
- optimized for Telegram readability.

### Story 8: Leave project notes
As the user, I want to save persistent project notes.

Example:
- `/note 登录模块近期容易回归，优先跑 auth 相关测试`

Expected behavior:
- the note is attached to the active project memory,
- and can influence future summaries or task planning.

---

## 7. Functional Scope

### 7.1 Telegram interface
The assistant must integrate with Telegram Bot API using long polling in the MVP.

#### Requirements
- Support only private chats in the MVP.
- Accept messages only from configured allowed Telegram user IDs.
- Reject or ignore unauthorized users.
- Send concise mobile-readable messages.
- Support incremental progress updates where practical.
- Support typing / progress feedback where practical.
- Prefer short status bursts over long walls of text.

#### Required commands
- `/use <project>`
- `/projects`
- `/ask <question>`
- `/do <task>`
- `/status`
- `/resume`
- `/approve`
- `/reject`
- `/diff`
- `/memory`
- `/note <text>`
- `/cancel`
- `/help`

### 7.2 Project registry
The assistant must maintain a local registry of allowed projects.

Each project entry must include:
- `name`
- `path`
- `default_branch`
- `test_command`
- optional `dev_command`
- `allowed_directories`
- optional `description`
- optional `stack`
- optional `architecture_notes`
- optional `tags`
- `last_codex_session_id`

#### Requirements
- Every task must be tied to one registered project.
- The active project must be set explicitly or inferred from previously selected project state.
- The assistant must never operate outside approved directories.
- The project registry must persist across restarts.
- The registry should be stored in YAML or JSON and remain human-editable.

Example:

```yaml
projects:
  myapp:
    path: /Users/you/work/myapp
    default_branch: main
    test_command: pnpm test
    dev_command: pnpm dev
    allowed_directories:
      - /Users/you/work/myapp
    description: Main web application
    stack: Next.js + TypeScript
    architecture_notes: docs/architecture.md
    tags: [web, auth]
    last_codex_session_id: null
```

### 7.3 Project-centric session continuity
The system must preserve continuity by project, not only by Telegram chat.

#### Requirements
- Maintain an active project per authorized user/chat context.
- Persist the latest Codex session identifier per project.
- Persist the latest resumable task per project.
- Persist project summaries and recent task summaries.
- Support resuming the last task for the current project.
- Avoid mixing contexts between repositories.

### 7.4 Codex CLI integration
Codex CLI is the execution backend.

#### Requirements
- Run Codex in the selected project working directory.
- Prefer non-interactive execution for orchestrated tasks.
- Prefer machine-readable output when available.
- Capture and persist important execution metadata.
- Record Codex session identifiers when available.
- Support follow-up execution against a previous Codex session where possible.
- Separate read-oriented asks from write-oriented tasks when practical.

#### Preferred execution patterns
Read-oriented ask example:

```bash
codex exec \
  --cd /path/to/project \
  --json \
  --sandbox workspace-write \
  --ask-for-approval on-request \
  "Explain the login flow and recent auth risk areas"
```

Write-oriented task example:

```bash
codex exec \
  --cd /path/to/project \
  --json \
  --sandbox workspace-write \
  --ask-for-approval on-request \
  "Investigate login API 500 errors, propose the smallest safe fix, run relevant tests, and summarize the diff"
```

Resume example:

```bash
codex exec resume --last "Continue the previous task and report remaining blockers"
```

### 7.5 Progress feedback
The assistant should feel responsive from a phone.

#### Requirements
- Immediately acknowledge `/ask` and `/do` requests.
- Emit short phase-oriented progress updates when possible.
- Example progress states:
  - validating project,
  - loading project memory,
  - starting Codex,
  - reading repository state,
  - running tests,
  - waiting for approval,
  - summarizing results.
- Keep updates concise.
- Avoid spamming low-value internal details.

### 7.6 Approval flow
Risky work must pause for explicit approval.

#### Requirements
- A task may enter `waiting_approval` state.
- Approval messages must explain what is blocked and why.
- `/approve` continues the blocked task if possible.
- `/reject` marks the blocked action rejected and updates task state.
- Approval decisions must be stored in durable state.

### 7.7 Diff and result summaries
The assistant must produce mobile-friendly output.

#### Requirements
- Summarize changed files.
- Summarize intent of changes.
- Summarize test results.
- Prefer structured short sections over raw command output.
- Preserve raw execution logs separately for audit/debugging.

### 7.8 Memory and notes
The system must preserve structured memory.

#### Memory layers
1. User-level memory
   - default project,
   - reply language preference,
   - answer style preference,
   - risk preference.

2. Project-level memory
   - project summary,
   - architecture notes,
   - common commands,
   - coding conventions,
   - known issues,
   - recent task summaries,
   - owner notes,
   - last Codex session ID.

3. Task-level state
   - original request,
   - task status,
   - latest summary,
   - approval state,
   - last test result,
   - next step,
   - error summary,
   - relevant artifacts.

#### Requirements
- Memory should be structured, not just raw transcript replay.
- Project notes added via `/note` must persist.
- `/memory` should return a concise project memory summary.
- Memory updates should be conservative and explicit.

### 7.9 Audit logging
The system must be auditable.

#### Requirements
- Log important state transitions.
- Log task creation, execution start, execution end, approval requests, approval decisions, cancellation, and resume.
- Log which project and task were involved.
- Keep sensitive values redacted where appropriate.
- Store audit data locally.

### 7.10 Cancellation and failure handling
The system must handle interruptions clearly.

#### Requirements
- `/cancel` should stop or mark the current task as cancelled when possible.
- Failures must update task state to `failed` with an error summary.
- Telegram should receive a concise explanation of failures.
- A failed task should remain inspectable.

---

## 8. Data Model Requirements

### 8.1 Core entities
At minimum, the system should model:

- authorized users,
- projects,
- chat context,
- tasks,
- approvals,
- project state,
- project memory,
- task artifacts,
- audit log entries.

### 8.2 Project state
The system should persist current project state fields such as:

- `project_name`
- `last_codex_session_id`
- `last_task_id`
- `current_branch`
- `working_tree_dirty`
- `last_task_summary`
- `last_test_status`
- `last_test_command`
- `pending_approval_task_id`
- `next_step`
- `updated_at`

### 8.3 Task state machine
Recommended statuses:

- `created`
- `queued`
- `running`
- `waiting_approval`
- `completed`
- `failed`
- `cancelled`
- `rejected`

---

## 9. Suggested Storage Strategy

Use:

- SQLite for durable structured state,
- YAML for project registry,
- JSON or markdown files for some summaries if convenient,
- local log files for execution traces if needed.

The persisted state should remain easy to inspect and back up.

---

## 10. Suggested SQLite Schema

The exact schema can evolve, but the MVP should cover at least the following tables.

### `users`
- `id`
- `telegram_user_id`
- `username`
- `is_active`
- `created_at`
- `updated_at`

### `chat_context`
- `id`
- `telegram_chat_id`
- `user_id`
- `active_project_name`
- `created_at`
- `updated_at`

### `projects`
- `id`
- `name`
- `path`
- `default_branch`
- `test_command`
- `dev_command`
- `allowed_directories_json`
- `description`
- `stack`
- `architecture_notes`
- `last_codex_session_id`
- `created_at`
- `updated_at`

### `project_memory`
- `id`
- `project_id`
- `summary`
- `common_commands`
- `coding_conventions`
- `known_issues`
- `owner_notes`
- `recent_task_summaries`
- `updated_at`

### `project_state`
- `id`
- `project_id`
- `last_task_id`
- `current_branch`
- `working_tree_dirty`
- `last_task_summary`
- `last_test_status`
- `last_test_command`
- `pending_approval_task_id`
- `next_step`
- `updated_at`

### `tasks`
- `id`
- `user_id`
- `project_id`
- `telegram_chat_id`
- `task_type`
- `original_request`
- `status`
- `codex_session_id`
- `latest_summary`
- `last_error_summary`
- `needs_approval`
- `created_at`
- `updated_at`
- `completed_at`

### `approvals`
- `id`
- `task_id`
- `status`
- `reason`
- `requested_at`
- `resolved_at`

### `task_artifacts`
- `id`
- `task_id`
- `artifact_type`
- `path`
- `metadata_json`
- `created_at`

### `audit_logs`
- `id`
- `user_id`
- `project_id`
- `task_id`
- `event_type`
- `message`
- `metadata_json`
- `created_at`

---

## 11. Security Requirements

These are mandatory.

### 11.1 Access control
- Only configured Telegram user IDs may use the assistant.
- Unauthorized users must not be able to trigger any repo access or Codex execution.

### 11.2 Project binding
- Every `/ask`, `/do`, `/resume`, `/diff`, `/note`, and `/memory` operation must be associated with a registered project.
- If no active project is set, the user should be prompted to select one.

### 11.3 Directory sandboxing
- The system must verify that requested execution paths are inside approved project directories.
- Path normalization and traversal prevention are required.
- The assistant must not silently widen path access.

### 11.4 Conservative execution defaults
- Prefer conservative defaults.
- Require approval for high-risk operations where practical.
- Keep the initial scope limited to workspace-bounded project operations.

### 11.5 Secret hygiene
- Avoid leaking environment secrets, tokens, or confidential file contents into logs or Telegram replies.
- Redact when practical.

### 11.6 Auditability
- Important actions must be recorded in audit logs.

---

## 12. Technical Constraints

- Language: Python 3.12
- Runtime: single long-running local process
- Persistence: SQLite + YAML/JSON
- Telegram transport: long polling for MVP
- Architecture: modular but lightweight
- Deployment: local machine only by default
- Dependencies: keep minimal and justified

Do not introduce:
- a web framework unless truly necessary,
- a background queueing platform,
- a distributed scheduler,
- a microservice architecture,
- or heavy enterprise abstractions.

---

## 13. Suggested High-Level Architecture

A lightweight modular architecture is preferred.

Recommended modules:

- `telegram_adapter`
- `auth_guard`
- `command_router`
- `project_registry`
- `state_store`
- `memory_store`
- `codex_runner`
- `approval_service`
- `progress_reporter`
- `summary_formatter`
- `audit_logger`

The system should still run as one local process.

---

## 14. Interaction Design

Telegram responses should be optimized for phone reading.

### Message style guidance
- Prefer short sections.
- Prefer bullets over dense paragraphs when status-heavy.
- Lead with the conclusion.
- Show the active project clearly.
- Show next action clearly.

### Example status shape

```text
Project: myapp
Task: investigating login API 500
State: waiting for approval
Branch: feature/login-fix
Tests: 2 failed
Next: approve patch application or reject
```

### Example progress shape

```text
Working on myapp...
- loaded project memory
- started Codex
- checking auth middleware
- running auth-related tests
```

---

## 15. Implementation Phases

### Phase 1: Minimum end-to-end flow
Build the smallest working version that proves the core path.

Must include:
- Telegram bot with long polling,
- allowlisted single-user access,
- project registry loading,
- `/projects`, `/use`, `/do`, `/status`, `/help`,
- Codex execution in selected project,
- SQLite task persistence,
- concise Telegram result replies,
- basic audit logging.

### Phase 2: Continuity and statefulness
Extend the MVP with project-centric continuity.

Must include:
- `/ask`, `/resume`, `/cancel`, `/diff`,
- save and reuse last Codex session ID,
- project state tracking,
- project memory summaries,
- resumable task lookup,
- better progress updates.

### Phase 3: Approval and richer memory
Add controlled safety and better long-term context.

Must include:
- `/approve`, `/reject`, `/note`, `/memory`,
- waiting approval state,
- approval persistence,
- project notes,
- stronger mobile-friendly summaries,
- improved audit entries.

### Phase 4: Polish and maintainability
Only after the core works.

May include:
- migrations,
- better artifact storage,
- improved diff summarization,
- startup health checks,
- stronger redaction,
- optional Telegram inline action buttons.

---

## 16. Acceptance Criteria

The implementation is acceptable when all of the following are true.

### Project selection
- `/projects` lists registered projects.
- `/use myapp` sets the active project.
- Subsequent commands operate on `myapp` by default.

### Safe routing
- Unregistered projects are rejected.
- Unapproved paths are rejected.
- Unauthorized users cannot use the bot.

### Codex execution
- `/do <task>` creates a durable task record.
- Codex runs in the selected project directory.
- The system stores enough state to inspect what happened.
- A concise result summary is returned.

### Continuity
- `/status` returns active project, last task status, and next step.
- `/resume` can continue the latest resumable task for the active project when possible.
- The latest Codex session ID is persisted per project.

### Auditability
- Important events are written to durable audit logs.

### Mobile usability
- Result and status messages are concise and readable in Telegram.
- The assistant provides visible progress for longer tasks.

### Restart durability
- Restarting the service does not lose project registry, task history, project state, or memory.

---

## 17. Explicit Inspiration To Incorporate

The implementation should intentionally incorporate these proven ideas:

- project-level session continuity,
- directory sandboxing and path validation,
- SQLite-backed persistence,
- progress-oriented Telegram interaction,
- command-oriented control flow alongside natural language task requests,
- audit logging of important task and approval events.

At the same time, the implementation should intentionally avoid premature complexity such as:

- multi-mode terminal emulation,
- multi-user platform features,
- webhook-first deployment,
- large tool/plugin systems,
- file upload and archive workflows in the MVP.

---

## 18. Delivery Instructions For Codex

When implementing this project:

1. First summarize your understanding of the requirements.
2. Then propose the directory structure.
3. Then propose the SQLite schema and key data models.
4. Then produce a phased implementation plan.
5. Then implement Phase 1 first.
6. Do not skip directly to advanced features.
7. Keep the code easy to read.
8. If making an assumption, state it clearly.
9. Prefer small working steps over large speculative rewrites.

