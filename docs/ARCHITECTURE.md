# OpenFish Architecture

## Overview

OpenFish is a single-process, local-first assistant:

- Telegram is the remote UI.
- The service runs on the owner's machine.
- Task/project/audit state is stored in local SQLite.
- Codex CLI executes inside the selected project directory.

## Architecture Diagram

![OpenFish Architecture](ARCHITECTURE_EN.png)

## Module View

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

## Runtime Flow

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

## Persistence Boundary

- Local SQLite: users, active project, tasks, approvals, schedules, memory, audit.
- `projects.yaml`: project registry and allowed paths.
- Local filesystem: source repos, logs, uploaded temp files.

This keeps continuity at project level across service restarts.
