<p align="center">
  <img src="docs/openfish_trending_icon_animated.svg" alt="OpenFish Logo" width="220" />
</p>

<h1 align="center">OpenFish（小鱼）</h1>
<p align="center"><strong>单用户、Telegram 驱动、本机运行的 Codex 远程助手</strong></p>

<p align="center">
  <a href="README.md">English</a> |
  <a href="LICENSE">MIT License</a> |
  <a href="CONTRIBUTING.md">贡献指南</a> |
  <a href="SECURITY.md">安全策略</a> |
  <a href="CHANGELOG.md">更新日志</a>
</p>

<p align="center">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-1f6feb" />
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white" />
  <img alt="PRs" src="https://img.shields.io/badge/PRs-welcome-2ea043" />
  <img alt="Architecture" src="https://img.shields.io/badge/Architecture-Single--Process-7a3cff" />
  <img alt="Powered by" src="https://img.shields.io/badge/Powered%20by-Codex%20CLI-d97706" />
</p>

OpenFish 面向一个可信 Owner，目标是让你离开工位时也能通过 Telegram 持续推进本地项目开发。
系统坚持本地优先：代码、执行、状态、审批、审计都留在你的机器上。

## 架构

### 模块视图

```mermaid
flowchart LR
    U[Telegram 用户] --> TG[Telegram Bot API]
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
    CCLI --> REPO[本地项目仓库]

    TS --> DB[(SQLite)]
    AU --> DB
    PR --> CFG[projects.yaml]

    SCH[scheduler.py] --> TS
    SCH --> R
```

### 运行流程

```mermaid
sequenceDiagram
    participant User as Telegram用户
    participant Adapter as Telegram适配层
    participant Router as CommandRouter
    participant Store as TaskStore
    participant Codex as CodexRunner

    User->>Adapter: /ask 或 /do
    Adapter->>Router: CommandContext
    Router->>Store: 创建任务并标记 running
    Router->>Codex: 在当前项目执行请求
    Codex-->>Router: summary/session/exit_code
    Router->>Store: 完成任务并更新项目状态
    Router-->>Adapter: CommandResult
    Adapter-->>User: 手机端友好摘要
```

## 产品定位

OpenFish 适合：

- 单用户场景
- 以项目为边界的连续性管理
- 默认保守的执行策略
- 手机端可读的简洁交互

OpenFish 不做：

- 多用户 Bot 平台
- 公网远程 Shell
- 云端编排系统

## 核心能力

- 项目生命周期：查看、切换、新增、停用、归档
- 任务生命周期：`/ask`、`/do`、`/resume`、`/retry`、`/cancel`
- 定时任务：新增/查看/触发/暂停/启用/删除
- 审批流程：`/approve`、`/reject`
- 项目记忆：笔记、任务摘要、状态快照
- 文件安全分析：上传后按后缀/大小/路径规则处理

## Telegram 交互演进

- 高频主键盘：`项目`、`提问`、`执行`、`状态`、`继续`、`变更`、`定时`、`更多`、`帮助`
- 审批流程优先按钮化，备注/原因向导也绑定显式 `approval_id`
- 新增项目、定时任务、模板执行、审批备注/拒绝原因都支持可恢复的分步向导
- chat 级 UI 模式支持精简/详细两种信息密度
- `状态`、`项目`、`定时`、`审批`、`更多` 这些卡片会优先更新已有消息，而不是不断堆新消息
- 发送链路增加短窗口去重和最近消息引用跟踪，减少重复输出

## ><> CLI 快速开始

先通过 PyPI 安装 `><> openfish`：

```bash
pip install openfish
```

然后统一通过 `><> openfish` CLI 使用：

```bash
openfish install
openfish configure
openfish check
openfish start
```

`><>` 如果你是在源码仓库里做开发，需要 editable 安装，再使用：

```bash
pip install -e ./mvp_scaffold
```

`><>` 当前主生命周期命令已经是原生 CLI：

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

`><>` 更新行为现在按安装模式区分：

- 仓库模式：`openfish update` 走 git 自更新
- 包/home 模式：使用 `python -m pip install --upgrade openfish`

`><>` 如果你想把运行时数据放到用户目录，而不是仓库目录，可以先初始化 home 模式：

```bash
openfish init-home
export OPENFISH_HOME=~/.config/openfish
openfish check
openfish start
```

`><>` 卸载命令入口：

```bash
openfish uninstall
```

如果连运行时配置和数据也要一起清理：

```bash
openfish uninstall --purge-runtime
```

`><>` 如果你还不知道自己的 Telegram 用户 ID，先给 bot 发 `/start`，再执行：

```bash
openfish tg-user-id
```

`><>` 旧脚本入口仍保留兼容：

```bash
bash mvp_scaffold/scripts/install_start.sh start
```

## ><> Docker 运行

仓库已经提供 Docker 独立运行模式，可用于长期自托管部署：

```bash
openfish docker-init
```

当前 Docker 模式已经改成独立运行态：

- OpenFish home 固定在 Docker volume `/var/lib/openfish`
- 默认项目根目录固定为 `/workspace/projects`
- Codex 登录态保存在 Docker volume `/root/.codex`
- 运行时状态、日志、SQLite、`projects.yaml` 都放在 named volumes
- 不再直接复用宿主机仓库里的 `.env`、`projects.yaml`、`mvp_scaffold/data`

Docker 是可选部署方式。对个人本机使用场景，仍建议优先使用 `openfish` CLI。

`><>` 可用的 Docker 辅助命令：

- `openfish docker-init`
- `openfish docker-configure`
- `openfish docker-up`
- `openfish docker-down`
- `openfish docker-health`
- `openfish docker-logs`
- `openfish docker-ps`
- `openfish docker-login-codex`
- `openfish docker-codex-status`

`><>` 推荐流程：

1. `openfish docker-init`
2. `openfish docker-login-codex`
3. `openfish docker-codex-status`

`openfish docker-init` 是最短路径：如有需要会先执行 `docker-configure`，然后启动容器，再自动执行一次 `docker-health`。

如果需要给容器内 Codex 完成登录：

```bash
openfish docker-login-codex
openfish docker-codex-status
```

`openfish docker-configure` 会写入 Docker 专用配置文件 `.openfish.docker.env`，并引导填写：

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_TELEGRAM_USER_IDS`
- 可选的 `DEFAULT_PROJECT`
- 可选的 bootstrap 项目 key / name

`openfish docker-login-codex` 支持：

- 官方 device auth 登录
- 导入本机 `~/.codex/auth.json` 或任意 auth.json 路径
- 直接粘贴原始 `auth.json` 内容

## ><> 命令总览

核心命令：

- `/projects`, `/use <project>`, `/status`
- `/ask <question>`, `/do <task>`, `/resume [task_id] [instruction]`
- `/approve [note]`, `/reject [reason]`, `/cancel`
- `/diff`, `/memory`, `/note <text>`, `/help`

扩展命令：

- `/project-root [abs_path]`
- `/project-add`, `/project-disable`, `/project-archive`
- `/skills`, `/skill-install`
- `/schedule-add`, `/schedule-list`, `/schedule-run`, `/schedule-pause`, `/schedule-enable`, `/schedule-del`
- `/start`, `/last`, `/retry`, `/upload_policy`

快捷按钮覆盖全部命令能力：

- 无参数命令可直接点击执行
- 高频有参操作会进入可恢复的分步向导

## 文档导航

面向使用者：

- 持久化说明：[docs/PERSISTENCE_ARCHITECTURE.md](docs/PERSISTENCE_ARCHITECTURE.md)
- 安装部署与使用手册：[docs/安装部署和使用手册.md](docs/安装部署和使用手册.md)

## 仓库结构

- 运行主目录：`mvp_scaffold/`
- 文档目录：`docs/`
- 配置样例：`env.example`, `projects.example.yaml`
- 数据库结构：`schema.sql`

## 安全提示

- Token 若出现在日志/截图中，请立即轮换。
- 不要提交 `.env`、运行时数据目录、含敏感信息的本地配置。
- 项目路径授权建议最小化并显式配置。
