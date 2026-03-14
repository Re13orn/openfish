# OpenFish Autopilot V1 Design

## Goal

Autopilot is a long-task execution mode for OpenFish.

It is designed to remove the low-value human loop where the owner repeatedly sends:

- "继续"
- "开始"
- "请进行下一步"

The mode introduces two Codex roles:

- `A`: supervisor
- `B`: worker

The worker does the task.
The supervisor inspects the worker's staged output and decides whether the worker should continue, stop, or escalate.

The owner is not expected to drive the task round by round.
The owner only keeps:

- observability
- pause/stop control
- optional takeover later

## Product Boundary

Autopilot is appropriate for:

- long-running coding tasks
- multi-step debugging or implementation work
- tasks that often stall at stage boundaries

Autopilot is not appropriate for:

- short one-shot tasks
- destructive high-risk operations by default
- tasks that require frequent product or business decisions from a human

Autopilot must be explicitly enabled.
It is not the default `/do` behavior.

## Role Model

### Supervisor (`A`)

Responsibilities:

- inspect the worker result
- determine whether meaningful progress happened
- decide whether the task should continue
- give the worker one clear next instruction when continuation is justified

Non-responsibilities:

- directly doing the implementation work
- directly modifying code as the main executor

### Worker (`B`)

Responsibilities:

- perform the actual task
- produce staged summaries
- report current state, blockers, and recommended next step

Non-responsibilities:

- making the final completion decision
- deciding whether the loop should continue indefinitely

### Human

Default role:

- observer
- stop/pause operator
- optional manual takeover

The human should not be required to repeatedly send continuation prompts.

## Core Loop

One autopilot cycle must be strictly linear.
No free-form A/B cross-talk.

Cycle:

1. `B` executes a stage of work
2. `B` emits structured output
3. `A` evaluates the worker result
4. `A` emits a structured decision
5. the system updates counters and either:
   - sends a new instruction to `B`
   - stops
   - pauses for human input

## Run State Machine

### `autopilot_run.status`

- `created`
- `running_worker`
- `running_supervisor`
- `paused`
- `completed`
- `blocked`
- `needs_human`
- `stopped`
- `failed`

### `autopilot_run.current_phase`

- `worker`
- `supervisor`
- `idle`

### State Transitions

#### Create

`/autopilot <goal>`

- create run with `created`
- initialize supervisor and worker sessions
- transition to `running_worker`

#### Worker stage complete

- persist worker event
- transition to `running_supervisor`

#### Supervisor decision

If supervisor decides `continue`:

- persist supervisor event
- update counters
- transition to `running_worker`

If supervisor decides `complete`:

- transition to `completed`

If supervisor decides `blocked`:

- transition to `blocked`

If supervisor decides `needs_human`:

- transition to `needs_human`

#### Human control

- `pause` -> `paused`
- `resume` -> normally `running_worker`
- `stop` -> `stopped`

#### System error

- unrecoverable execution failure -> `failed`

## Structured Output Contracts

Structured output is mandatory.
The loop must not rely on parsing loose natural language.

## Worker Output Schema

The worker must emit JSON with this shape:

```json
{
  "completed_work": "string",
  "current_state": "string",
  "remaining_work": "string",
  "blockers": "string",
  "recommended_next_step": "string",
  "progress_made": true,
  "task_complete": false
}
```

Constraints:

- `completed_work` must be non-empty
- `current_state` must be non-empty
- `progress_made` must be boolean
- `task_complete` only means the worker believes the goal is complete
- final completion is still decided by the supervisor

## Supervisor Output Schema

The supervisor must emit JSON with this shape:

```json
{
  "decision": "continue",
  "reason": "string",
  "progress_summary": "string",
  "progress_made": true,
  "confidence": "medium",
  "next_instruction_for_b": "string"
}
```

Constraints:

- `decision` must be one of:
  - `continue`
  - `complete`
  - `blocked`
  - `needs_human`
- if `decision == continue`, `next_instruction_for_b` must be non-empty
- if `decision != continue`, `next_instruction_for_b` should be empty
- `progress_made` must be boolean
- `confidence` must be one of:
  - `low`
  - `medium`
  - `high`

## Stop Conditions

Autopilot must stop early and deterministically.
It must not rely on humans noticing a loop.

### Default Limits

- `max_cycles = 100`
- `max_cycles_hard_limit = 200`
- `max_no_progress_cycles = 2`
- `max_same_instruction_cycles = 2`

### Immediate Stop Triggers

Stop the run if any of the following is true:

1. supervisor decision is `complete`
2. supervisor decision is `blocked`
3. supervisor decision is `needs_human`
4. `cycle_count >= max_cycles`
5. `no_progress_cycles >= 2`
6. `same_instruction_cycles >= 2`

`max_cycles = 100` is an upper bound, not a target.
The run should stop as soon as it is clear that continued execution is not justified.

## Progress and Loop Detection

### Progress

V1 uses explicit structured flags first:

- worker `progress_made`
- supervisor `progress_made`

The system may later add artifact-aware progress checks such as:

- diff changed
- new files created
- error mode changed
- state moved from analysis to execution or verification

V1 should keep the first implementation simple.

### Repeated Instruction Detection

V1 should use a simple fingerprint for `next_instruction_for_b`:

- trim
- lowercase
- collapse whitespace
- truncate to 200 chars

If the fingerprint matches the previous one:

- increment `same_instruction_cycles`

Otherwise:

- reset `same_instruction_cycles`

## Data Model

## Table: `autopilot_runs`

Suggested columns:

- `id`
- `project_id`
- `chat_id`
- `created_by_user_id`
- `goal`
- `status`
- `supervisor_session_id`
- `worker_session_id`
- `current_phase`
- `cycle_count`
- `max_cycles`
- `no_progress_cycles`
- `same_instruction_cycles`
- `last_instruction_fingerprint`
- `last_decision`
- `last_worker_summary`
- `last_supervisor_summary`
- `paused_reason`
- `stopped_by_user_id`
- `created_at`
- `updated_at`

## Table: `autopilot_events`

Suggested columns:

- `id`
- `run_id`
- `cycle_no`
- `actor`
- `event_type`
- `summary`
- `payload_json`
- `created_at`

## Session Model

Supervisor and worker must use separate Codex sessions.

Required fields:

- `supervisor_session_id`
- `worker_session_id`

The two roles must never share the same session.

The worker session is the execution context.
The supervisor session is the evaluation context.

## Telegram Command Surface

V1 should keep the command set small.

Suggested commands:

- `/autopilot <goal>`
- `/autopilot-status [id]`
- `/autopilot-stop [id]`
- `/autopilot-pause [id]`
- `/autopilot-resume [id]`
- `/autopilot-context [id]`

Optional debug command for implementation phase:

- `/autopilot-step [id]`

`/autopilot-step` is useful during development because it allows validation of one worker-supervisor round without committing to the full loop.

## Telegram UI Requirements

### Status Card

`/autopilot-status` should show:

- run id
- goal
- status
- current phase
- cycle count
- latest supervisor decision
- latest worker summary
- no-progress counter
- repeated-instruction counter

### Control Buttons

Minimum control surface:

- stop
- pause
- resume
- view context

### Context Card

`/autopilot-context` should show:

- supervisor session id
- worker session id
- which role is currently active
- latest worker output summary
- latest supervisor output summary
- current stop-condition counters

## Prompt Responsibilities

## Supervisor Prompt

The supervisor prompt must explicitly constrain the role:

- do not act as the primary executor
- do not emit vague "continue" output
- decide whether the worker should continue, stop, or escalate
- if continuing, emit one explicit minimal next instruction
- if there is no meaningful progress, stop or escalate instead of looping

## Worker Prompt

The worker prompt must explicitly constrain the role:

- act as the main executor
- attempt to keep moving instead of frequently returning control to the human
- after each stage, emit the required JSON status block
- summarize blockers clearly

## Execution Model

V1 should avoid concurrency inside a single run.

At any moment, a run may have:

- one active worker step
- or one active supervisor step

It must not run both at the same time.

This reduces:

- state corruption risk
- unclear transitions
- cancellation complexity

## Service Structure

Suggested new modules:

- `autopilot_store.py`
- `autopilot_service.py`
- `autopilot_prompts.py`

Responsibilities:

### `autopilot_store.py`

- CRUD for `autopilot_runs`
- append/read `autopilot_events`

### `autopilot_service.py`

- initialize a run
- execute one worker step
- execute one supervisor step
- update counters
- apply stop-condition logic
- own the state transitions

### `autopilot_prompts.py`

- supervisor prompt template
- worker prompt template
- JSON contract definitions

## Codex Runner Reuse

V1 should reuse the existing Codex runner instead of inventing a new subprocess stack.

Expected reuse:

- worker -> existing project-scoped Codex execution
- supervisor -> separate project-scoped Codex execution in a different session

The main addition is orchestration, not a second transport layer.

## Human Intervention Policy

V1 should support:

- `pause`
- `resume`
- `stop`

V1 should not yet support a complex manual takeover prompt injection mechanism.

That can be added later after the state model proves stable.

## Suggested Implementation Order

1. migrations for `autopilot_runs` and `autopilot_events`
2. store layer
3. formatter and Telegram status shell
4. one-step execution path:
   - worker step
   - supervisor step
5. `/autopilot` create
6. `/autopilot-step` debug flow
7. full loop runner
8. `/autopilot-status`
9. `/autopilot-stop`, `/autopilot-pause`, `/autopilot-resume`
10. tests for stop conditions and session separation

## Test Plan

V1 must include tests for:

1. supervisor `continue` leads to next worker step
2. supervisor `complete` ends the run
3. supervisor `blocked` ends the run
4. supervisor `needs_human` ends the run in escalation state
5. `max_cycles` is enforced
6. repeated no-progress stops the run
7. repeated identical instruction stops the run
8. pause/resume/stop state transitions are correct
9. supervisor and worker sessions are distinct

## V1 Non-Goals

The following are intentionally out of scope for V1:

- multiple workers
- supervisor directly performing implementation work
- automatic model switching
- autonomous task tree creation
- complex manual takeover injection
- multi-project orchestration

## Summary

Autopilot V1 is a constrained supervisor-worker long-task mode.

It must be:

- explicit
- structured
- observable
- stoppable
- finite

If the implementation cannot preserve those properties, it should be reduced rather than made more autonomous.
