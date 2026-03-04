<p align="center">
  <img src="docs/logo.png" alt="OpenFish Logo" width="220" />
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

## 快速开始

```bash
cd mvp_scaffold
bash scripts/install_start.sh
```

在安装脚本中执行 `configure`，生成 `.env` 与 `projects.yaml` 后启动服务。

## 命令总览

核心命令：

- `/projects`, `/use <project>`, `/status`
- `/ask <question>`, `/do <task>`, `/resume [task_id] [instruction]`
- `/approve [note]`, `/reject [reason]`, `/cancel`
- `/diff`, `/memory`, `/note <text>`, `/help`

扩展命令：

- `/project-root [abs_path]`
- `/project-add`, `/project-disable`, `/project-archive`
- `/templates`, `/run`, `/skills`, `/skill-install`
- `/schedule-add`, `/schedule-list`, `/schedule-run`, `/schedule-pause`, `/schedule-enable`, `/schedule-del`
- `/start`, `/last`, `/retry`, `/upload_policy`

快捷按钮覆盖全部命令能力：

- 无参数命令可直接点击执行
- 需要参数的命令会进入输入引导模式，并对下一条消息自动补全命令前缀

## 文档导航

面向使用者：

- 安装部署与使用手册：[docs/安装部署和使用手册.md](docs/安装部署和使用手册.md)

内部文档：

- 规格说明：[docs/internal/SPEC.md](docs/internal/SPEC.md)
- Agent 设计约束：[docs/internal/AGENTS.md](docs/internal/AGENTS.md)
- 系统设计理念与开发历程：[docs/internal/系统设计理念与开发历程.md](docs/internal/系统设计理念与开发历程.md)
- 5 分钟精简路演版：[docs/internal/5分钟精简路演版.md](docs/internal/5分钟精简路演版.md)
- GitHub 开源发布清单：[docs/internal/GitHub开源发布清单.md](docs/internal/GitHub开源发布清单.md)

## 仓库结构

- 运行主目录：`mvp_scaffold/`
- 文档目录：`docs/`
- 内部文档：`docs/internal/`
- 配置样例：`env.example`, `projects.example.yaml`
- 数据库结构：`schema.sql`

## 安全提示

- Token 若出现在日志/截图中，请立即轮换。
- 不要提交 `.env`、运行时数据目录、含敏感信息的本地配置。
- 项目路径授权建议最小化并显式配置。
