OpenFish
========

OpenFish is a single-user, Telegram-driven local Codex assistant.

It lets one trusted owner control local repositories from Telegram while execution, approvals, state, and audit data remain on the local machine.

Core capabilities
-----------------

- Project lifecycle: list, select, add, disable, archive
- Task lifecycle: ask, do, resume, retry, cancel
- Scheduling: periodic task add/list/run/pause/enable/delete
- Approval flow: approve/reject continuation
- Project memory: notes, recent summaries, paginated memory view
- Codex session browser: OpenFish sessions and local native sessions
- MCP management: list, inspect, enable, disable
- Service controls: restart, logs, update-check, self-update
- CLI runtime: `openfish install`, `configure`, `check`, `start`

Quick start
-----------

Install:

```bash
pip install openfish
```

Repository mode:

```bash
openfish install
openfish configure
openfish check
openfish start
```

Home runtime mode:

```bash
openfish init-home
export OPENFISH_HOME=~/.config/openfish
openfish install
openfish configure
openfish check
openfish start
```

Common commands
---------------

- `openfish install`
- `openfish uninstall`
- `openfish configure`
- `openfish init-home`
- `openfish check`
- `openfish start`
- `openfish stop`
- `openfish restart`
- `openfish status`
- `openfish logs`
- `openfish update-check`

Scope
-----

OpenFish is built for:

- one trusted owner
- local-first execution
- project-scoped continuity
- Telegram as the primary control surface

OpenFish is not:

- a multi-user bot platform
- a public remote shell
- a cloud orchestration system

Links
-----

- Homepage: https://github.com/Re13orn/openfish
- README: https://github.com/Re13orn/openfish/blob/main/README.md
- 中文说明: https://github.com/Re13orn/openfish/blob/main/README_CN.md
- Security: https://github.com/Re13orn/openfish/blob/main/SECURITY.md
- Changelog: https://github.com/Re13orn/openfish/blob/main/CHANGELOG.md
