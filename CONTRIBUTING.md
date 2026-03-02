# Contributing to OpenFish

Thanks for your interest in contributing.

## Scope and Philosophy

OpenFish is intentionally:

- single-user,
- local-first,
- project-centric,
- conservative by default.

Please keep contributions aligned with these constraints. Large platform-style expansions should be proposed first in an issue.

## Development Setup

```bash
cd mvp_scaffold
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run Checks

```bash
cd mvp_scaffold
bash scripts/ci_local.sh
```

## Pull Request Guidelines

- Keep PRs focused and small.
- Add tests for behavior changes.
- Keep user-facing Telegram messages concise and mobile-friendly.
- Avoid introducing multi-user logic or remote execution infrastructure unless explicitly discussed.
- Ensure no secrets, local DB files, or runtime logs are included.

## Commit Message Suggestion

Use clear, imperative messages, for example:

- `feat(router): add /retry support for latest ask/do task`
- `fix(telegram): retry transient send timeout errors`
- `docs: add open source contribution guide`
