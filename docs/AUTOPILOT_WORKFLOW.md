# OpenFish Autopilot Workflow

## Overview

Autopilot is a supervisor-worker execution loop:

- `A`: supervisor
- `B`: worker
- `Human`: observer, controller, optional takeover

The human starts the run once.
After that, the system keeps driving the loop until:

- the task is complete
- the run is blocked
- human intervention is required
- the owner pauses or stops it

## Top-Level Flow

```mermaid
flowchart TD
    H[Human creates /autopilot goal] --> R[Create autopilot run]
    R --> S1[Initialize A session and B session]
    S1 --> W[Set status = running_worker]
    W --> B1[B worker executes one stage]
    B1 --> BO[B emits structured output]
    BO --> S2[Set status = running_supervisor]
    S2 --> A1[A supervisor evaluates B output]
    A1 --> AO[A emits structured decision]

    AO -->|continue| C1[Update counters and next instruction]
    C1 --> W

    AO -->|complete| DONE[Set status = completed]
    AO -->|blocked| BLOCKED[Set status = blocked]
    AO -->|needs_human| HUMAN[Set status = needs_human]

    H -->|pause| PAUSE[Set status = paused]
    H -->|stop| STOP[Set status = stopped]
    H -->|takeover| TAKEOVER[Inject new high-level instruction]

    PAUSE -->|resume| W
    PAUSE -->|single step| STEP[Run one worker-supervisor cycle]
    STEP --> PAUSE
    TAKEOVER --> W
```

## Cycle Detail

```mermaid
sequenceDiagram
    participant Human
    participant System
    participant B as Worker B
    participant A as Supervisor A

    Human->>System: /autopilot <goal>
    System->>System: create autopilot_run
    System->>B: start worker prompt
    B-->>System: worker JSON output
    System->>A: supervisor prompt with B output
    A-->>System: supervisor JSON decision

    alt decision = continue
        System->>System: increment cycle_count
        System->>System: update no_progress / same_instruction counters
        System->>B: next_instruction_for_b
    else decision = complete
        System->>System: set status = completed
    else decision = blocked
        System->>System: set status = blocked
    else decision = needs_human
        System->>System: set status = needs_human
    end

    Human->>System: /autopilot-context or /autopilot-status
    System-->>Human: current state, sessions, recent events
```

## State Machine

```mermaid
stateDiagram-v2
    [*] --> created
    created --> running_worker: initialize run
    running_worker --> running_supervisor: worker stage complete
    running_supervisor --> running_worker: supervisor = continue
    running_supervisor --> completed: supervisor = complete
    running_supervisor --> blocked: supervisor = blocked
    running_supervisor --> needs_human: supervisor = needs_human

    running_worker --> paused: human pause
    running_supervisor --> paused: human pause
    paused --> running_worker: resume
    paused --> paused: single step then re-pause

    running_worker --> stopped: human stop
    running_supervisor --> stopped: human stop
    paused --> stopped: human stop

    created --> failed: setup failure
    running_worker --> failed: unrecoverable worker failure
    running_supervisor --> failed: unrecoverable supervisor failure
```

## Stop Rules

```mermaid
flowchart TD
    D[Supervisor decision or loop counters updated] --> Q{Should stop?}
    Q -->|decision = complete| C[completed]
    Q -->|decision = blocked| B[blocked]
    Q -->|decision = needs_human| N[needs_human]
    Q -->|cycle_count >= max_cycles| M[blocked by max cycles]
    Q -->|no_progress_cycles >= 2| P[blocked by no progress]
    Q -->|same_instruction_cycles >= 2| R[blocked by repeated instruction]
    Q -->|no| CONT[continue loop]
```

## Human Control Surface

```mermaid
flowchart LR
    HS[/autopilot-status/] --> OBS[Observe run state]
    HC[/autopilot-context/] --> CTX[Inspect A/B sessions and recent events]
    HP[/autopilot-pause/] --> PZ[Pause background loop]
    HR[/autopilot-resume/] --> RS[Resume autonomous loop]
    HX[/autopilot-step/] --> SS[Run one cycle only]
    HT[/autopilot-takeover/] --> TK[Inject new high-level instruction]
    HSP[/autopilot-stop/] --> ST[Stop run]
```

## Mental Model

Keep the system model simple:

- `Human` decides the goal and only intervenes when needed
- `B` does the work
- `A` decides whether `B` should continue
- `System` enforces loop limits and stop rules

This is not free-form multi-agent chat.
It is a constrained execution loop with observability and control.
