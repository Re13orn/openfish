# OpenFish Pause and Restart Checklist

Last updated: 2026-03-19

## 1. Freeze Baseline

- create and push a final pause tag (example: `v1.3.0-pause`)
- record the exact commit SHA in release notes
- ensure working tree is clean (except intentionally ignored local folders)

## 2. Communicate Repository Status

- keep `README.md` and `README_CN.md` status messaging aligned with the current project direction
- keep issue/PR templates aligned with pause state
- clarify support scope: critical and security fixes only (if applicable)

## 3. Preserve Reproducibility

- pin dependencies in `mvp_scaffold` and verify lock behavior
- run local CI once and keep result snapshot:
  - command: `cd mvp_scaffold && bash scripts/ci_local.sh`
  - save pass/fail summary in an issue or release note
- verify no secrets are present in tracked files

## 4. Preserve Operations Knowledge

- keep a short restart runbook:
  - env prerequisites
  - start/check commands
  - release commands
- keep migration and schema notes current
- keep known-risk list current (autopilot, sandbox/approval defaults, MCP assumptions)

## 5. Backlog Triage Before Pause

- close or relabel non-actionable issues
- mark postponed items as `paused` / `next-direction`
- keep only high-signal issues for later restart

## 6. Restart Checklist (When Resuming)

- pull latest `main` and check baseline tag diff
- run local CI and smoke test Telegram flows:
  - `/home`
  - `/do`
  - `/autopilot`
  - `/autopilot-context`
  - `/schedule-add`
- verify Codex model list discovery works in runtime panel
- validate release pipeline before creating next version tag
