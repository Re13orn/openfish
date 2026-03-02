# First Codex Implementation Prompt

Please implement **Phase 1** of this project based on the repository documents `SPEC.md`, `AGENTS.md`, `README.md`, `projects.example.yaml`, and `schema.sql`.

## Important instructions
- Read `AGENTS.md` first and follow it.
- Read `SPEC.md` before coding.
- Do **not** implement everything at once.
- Only implement a clean, minimal, working **Phase 1**.
- Keep the architecture simple and single-process.
- Use Python 3.12.
- Use SQLite for durable state.
- Use Telegram long polling.
- Use Codex CLI as the execution engine.
- Default to conservative behavior.
- Do not add web UI, webhook hosting, multi-user support, or plugin systems.

## Phase 1 scope
Implement only the following:
1. Load configuration from environment variables.
2. Load project registry from YAML.
3. Initialize SQLite using the provided schema.
4. Start a Telegram bot using long polling.
5. Authenticate incoming messages against `ALLOWED_TELEGRAM_USER_IDS`.
6. Support these commands:
   - `/use <project>`
   - `/do <task>`
   - `/status`
   - `/help`
7. Persist minimal task records in SQLite.
8. Track the active project for the authorized user.
9. Execute Codex CLI for `/do` in the selected project's working directory.
10. Return concise Telegram-friendly summaries.
11. Write basic audit logs.

## Phase 1 behavior details
### `/use <project>`
- Validate the project exists in the YAML registry.
- Save it as the current default/active project for the authorized user.
- Reply with project name, path, default branch, and test command.

### `/status`
Return a concise status summary containing:
- active project
- project path
- last known Codex session id if any
- most recent task summary if any
- pending approval flag if any

### `/do <task>`
- Require an active project.
- Create a task row in SQLite.
- Write an audit log row.
- Run Codex CLI in the active project directory.
- Prefer machine-readable output if practical.
- Capture stdout/stderr.
- Save a useful final summary to the task record.
- Reply with a short summary suitable for Telegram.

### `/help`
Return a short help message listing the supported Phase 1 commands.

## Implementation preferences
- Create small modules with clear responsibilities.
- Avoid unnecessary abstraction.
- Use type hints where helpful.
- Include docstrings for key modules/functions.
- Keep logging readable.
- If Codex CLI integration has uncertainty, isolate it cleanly behind `codex_runner.py`.

## Expected output order
Before writing code:
1. Summarize your understanding of Phase 1.
2. List the files you will create or modify.
3. Describe the minimal control flow.

Then implement the code.

After implementation:
1. Explain how to run it locally.
2. List assumptions.
3. List what remains for Phase 2.
