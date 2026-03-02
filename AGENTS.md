# AGENTS.md

## Mission

Build and maintain a single-user, Telegram-driven personal Codex assistant that runs on the user's own computer.

The assistant is a practical remote coding tool, not a broad agent platform. It should let the owner talk to local projects from Telegram, route requests to the correct repository, invoke Codex safely, preserve project continuity, and return useful mobile-friendly summaries.

---

## What This Project Is

This project is:
- single-user,
- local-first,
- Telegram-driven,
- Codex-powered,
- project-centric,
- stateful across tasks,
- conservative by default,
- intentionally small and understandable.

This project is not:
- a multi-user service,
- an enterprise bot platform,
- a public remote shell,
- a cloud-native orchestration framework,
- a plugin marketplace,
- a terminal emulator inside Telegram.

When in doubt, optimize for one technical owner who values reliability, safety, and clarity.

---

## Product Intent

The owner already uses Codex locally, but wants to keep using it while away from the computer.

Telegram is only the remote interface.
The real system lives on the owner's machine.
The true continuity boundary is the project, not the chat window.

The system should preserve and expose:
- active project selection,
- project-level memory,
- latest resumable task,
- latest Codex session ID,
- pending approvals,
- recent task summary,
- next step.

---

## Non-Negotiable Product Principles

### 1. Single-user only
Design for one trusted owner and one allowlisted Telegram account.

Do not add:
- multi-user flows,
- roles,
- teams,
- organization models,
- shared project ownership.

### 2. Project-centric continuity
Persist continuity by project.

Do not treat Telegram chat history as the main memory model.
Prefer explicit structured project state and task state.
Keep project contexts isolated from each other.

### 3. Local-first execution
The assistant should run locally on the owner's machine.

State should stay local.
Repo access should stay local.
Execution should stay local.
Avoid introducing remote infrastructure unless explicitly requested.

### 4. Conservative by default
If there is uncertainty, prefer the safer and more explainable behavior.

Examples:
- prefer explanation before mutation,
- prefer explicit project binding,
- prefer approval gates for risky work,
- prefer limited directory access.

### 5. Small and understandable architecture
Prefer a single-process architecture with lightweight modules.
Avoid platform sprawl.
Avoid premature abstractions.
Avoid heavy frameworks unless they clearly simplify the implementation.

### 6. Mobile-friendly interaction
Telegram is a phone interface.
All user-facing messages should be concise, structured, and easy to scan.
Do not dump large logs into chat unless explicitly requested.

### 7. Auditable behavior
Important actions should be recorded clearly.
The owner should be able to inspect what happened after the fact.

---

## Required Architecture Shape

Prefer a modular single-process architecture with small components such as:
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

This should still run as one local service.
Do not turn it into a microservice system.
Do not introduce workers, brokers, or orchestration platforms unless explicitly requested.

---

## Core Product Behaviors

The finished system should let the owner:
- list projects,
- select a project,
- ask project questions,
- start Codex tasks,
- receive progress updates,
- inspect project and task status,
- inspect recent diffs,
- resume unfinished work,
- approve or reject risky steps,
- and store project notes.

Core commands should include:
- `/projects`
- `/use <project>`
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

---

## Security Rules

These rules are mandatory.

1. Only configured Telegram user IDs may use the system.
2. Every task must be tied to a registered project.
3. Never access unregistered directories.
4. Normalize and validate paths before use.
5. Default to conservative behavior.
6. Require explicit approval for risky work where practical.
7. Avoid leaking secrets in logs or chat output.
8. Record important state transitions in durable audit logs.
9. Support cancellation or rejection of blocked work.
10. Never silently widen execution scope.

If a proposed implementation weakens these guarantees, do not proceed without explicitly calling out the risk.

---

## Scope Control

Do not add the following unless explicitly requested:
- web frontend,
- dashboard,
- Slack / Discord / email support,
- public webhook-first deployment,
- multi-user support,
- plugin or skills marketplace,
- file uploads and archive extraction,
- rich terminal emulation inside Telegram,
- distributed background job systems,
- heavy enterprise permission systems,
- broad autonomous agent capabilities.

If a feature is interesting but outside current scope, mention it as a future enhancement note instead of implementing it.

---

## Persistence Guidance

Prefer:
- SQLite for structured durable state,
- YAML or JSON for registry/config files,
- local log files for execution traces or artifacts.

Persist enough state to recover continuity after restart.
At minimum preserve:
- active project per chat/user,
- project registry,
- project memory,
- project state,
- tasks,
- approvals,
- audit logs,
- latest Codex session IDs.

---

## Memory Model Guidance

Use three memory layers.

### User-level memory
Stable owner preferences such as:
- default project,
- preferred language,
- answer style,
- risk preference.

### Project-level memory
Durable repo-specific context such as:
- project summary,
- architecture notes,
- common commands,
- coding conventions,
- known issues,
- owner notes,
- recent task summaries,
- latest Codex session ID.

### Task-level state
Execution continuity such as:
- original request,
- task type,
- status,
- latest summary,
- pending approval,
- last test result,
- next step,
- error summary,
- related artifacts.

Do not rely on replaying full chat transcripts as the main continuity mechanism.
Prefer explicit summaries and structured fields.

---

## Project-Centric Continuity Rules

- Keep separate continuity for each project.
- Avoid cross-project contamination of context.
- Persist the latest resumable task per project.
- Persist the latest Codex session ID per project.
- Make `/resume` and `/status` operate against the active project.
- If no active project is selected, ask the user to choose one.

---

## Telegram Interaction Rules

User-facing messages should be optimized for a phone.

### Do
- acknowledge requests quickly,
- send short progress updates for long tasks,
- format outputs into short sections,
- put conclusion first,
- clearly show active project and next step.

### Do not
- dump raw logs into chat by default,
- produce walls of text,
- spam internal implementation details,
- hide important state such as approval blocking or failure state.

For longer work, prefer concise progress steps such as:
- validating project,
- loading memory,
- starting Codex,
- checking repo state,
- running tests,
- waiting for approval,
- summarizing results.

---

## Codex Integration Rules

- Use Codex CLI as the execution engine.
- Prefer non-interactive execution when orchestrating tasks.
- Prefer machine-readable output when available.
- Always run Codex inside the selected project directory.
- Save Codex session identifiers whenever available.
- Distinguish read-oriented asks from write-oriented tasks when practical.
- Persist meaningful execution summaries instead of only raw output.

Do not assume Codex alone is the full state manager.
The outer application must still maintain project state, task state, approvals, and audit logs.

---

## Approval Rules

Treat approvals as a first-class workflow state.

- A task may enter `waiting_approval`.
- Explain what is blocked and why.
- `/approve` should continue the blocked workflow when possible.
- `/reject` should reject or stop the blocked workflow.
- Persist approval decisions.
- Reflect approval state in `/status`.

---

## Audit Logging Rules

Record important actions such as:
- project selection,
- task creation,
- task start,
- progress milestone events when meaningful,
- approval requested,
- approval granted,
- approval rejected,
- task completion,
- task failure,
- task cancellation,
- resume events.

Keep audit logs durable and locally inspectable.
Redact sensitive values where practical.

---

## Implementation Order

Build in small phases.

### Phase 1
Implement the core end-to-end flow:
- Telegram long polling,
- allowlisted access,
- project registry,
- `/projects`, `/use`, `/do`, `/status`, `/help`,
- Codex execution in project context,
- SQLite task persistence,
- basic audit logging.

### Phase 2
Add continuity features:
- `/ask`, `/resume`, `/cancel`, `/diff`,
- project state tracking,
- save latest Codex session ID,
- better progress updates,
- resumable task lookup.

### Phase 3
Add memory and approval flow:
- `/approve`, `/reject`, `/note`, `/memory`,
- project notes,
- waiting approval state,
- approval persistence,
- better summaries.

Only after these should you consider extra polish.

---

## Coding Style

- Write clear, direct Python 3.12.
- Prefer explicit names.
- Keep functions focused.
- Keep modules small.
- Use comments where behavior is non-obvious.
- Prefer readability over cleverness.
- Avoid unnecessary abstraction layers.

If choosing between a short but opaque implementation and a slightly longer but clearer one, prefer clarity.

---

## Error Handling Expectations

- Fail clearly.
- Persist state before and after meaningful transitions.
- Mark failures in task state.
- Return concise error summaries to Telegram.
- Preserve enough context for later inspection.
- Make cancellation and rejection visible in stored state.

---

## Delivery Expectations For Codex

When implementing features:
1. summarize your understanding first,
2. propose the directory structure,
3. define the key data models,
4. implement in phases,
5. keep the MVP working at each stage,
6. state assumptions clearly,
7. avoid speculative expansion beyond scope.

Always build the smallest correct version first.

