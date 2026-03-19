# OpenFish Next Direction Notes

Last updated: 2026-03-19

This document captures the intended next-stage direction after the current repository pause.

## Product Goals

- reduce command-first interaction and strengthen natural-language operation in Telegram
- improve autonomous execution reliability with clearer controller policy
- increase user trust with better observability and resumability

## Priority Tracks

### 1) Natural-Language Entry Upgrade

- stronger intent detection and project inference
- lower friction for schedule, project switch, and follow-up actions
- preserve deterministic command path for power users

### 2) Autopilot Controller Rule-ization

- move repeated supervisor decisions to deterministic rules first
- call LLM supervisor only for ambiguous states
- reduce unnecessary takeover requests when worker can continue safely

### 3) Proactive Summary and Status

- periodic digest for task/autopilot/schedule outcomes
- one canonical status surface (avoid competing live cards)
- complete run history and replay-ready context for audits

## Non-goals (For Next Stage)

- multi-user tenancy platform
- cloud-only execution control plane
- unbounded plugin platform without explicit trust boundaries

## Exit Criteria for Restart

- a written architecture brief for the new direction
- a migration plan from current data and command model
- test strategy covering Telegram UX, autopilot loop, and scheduler observability
