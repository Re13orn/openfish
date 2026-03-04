<p align="center">
  <img src="docs/logo.png" alt="OpenFish Logo" width="220" />
</p>

<h1 align="center">OpenFish（小鱼）</h1>
<p align="center"><strong>单用户、Telegram 驱动、本机运行的 Codex 远程助手</strong></p>

<p align="center">
  <a href="README.md">English</a> |
  <a href="LICENSE">MIT License</a> |
  <a href="CONTRIBUTING.md">贡献指南</a> |
  <a href="CHANGELOG.md">更新日志</a>
</p>

OpenFish 面向一个可信 Owner，目标是让你离开工位时也能通过 Telegram 持续推进本地项目开发。
系统坚持本地优先：代码、执行、状态、审计都留在你的机器上。

## 项目定位

这是一个：

- 单用户
- 本地优先
- 项目连续性优先
- 默认保守（路径边界 + 审批流程）
- 手机端友好反馈

这不是一个：

- 多用户 Bot 平台
- 公网远程 Shell
- 云端编排系统
- 插件市场

## 核心能力

- 项目管理：查看、切换、新增、停用、归档
- 任务执行：`/ask`、`/do`、`/resume`、`/retry`、`/cancel`
- 定时任务：创建/查看/触发/暂停/启用/删除
- 项目记忆：摘要、笔记、最近任务
- 审批流：`/approve`、`/reject`
- 状态与变更：`/status`、`/diff`、`/last`
- 文件分析：文档上传后在项目临时目录做安全分析

## 快速开始

```bash
# 在仓库根目录执行
cd mvp_scaffold
bash scripts/install_start.sh
```

按向导执行 `configure`，生成 `.env` 与 `projects.yaml` 后即可启动服务。

## 命令总览

核心命令：

- `/projects`, `/use <project>`, `/status`
- `/ask <question>`, `/do <task>`, `/resume [task_id] [instruction]`
- `/approve [note]`, `/reject [reason]`, `/cancel`
- `/diff`, `/memory`, `/note <text>`, `/help`

扩展命令：

- 项目生命周期：`/project-add`, `/project-disable`, `/project-archive`
- 模板与技能：`/templates`, `/run`, `/skills`, `/skill-install`
- 定时任务：`/schedule-add`, `/schedule-list`, `/schedule-run`, `/schedule-pause`, `/schedule-enable`, `/schedule-del`
- 其他：`/start`, `/last`, `/retry`, `/upload_policy`

快捷按钮已覆盖全部命令能力：

- 无参数命令可直接点击执行
- 带参数命令进入输入引导模式，下一条消息自动补全命令前缀

## 文档索引

- 安装部署和使用手册：[docs/安装部署和使用手册.md](docs/安装部署和使用手册.md)
- 系统设计理念与开发历程：[docs/系统设计理念与开发历程.md](docs/系统设计理念与开发历程.md)
- 5 分钟精简路演版：[docs/5分钟精简路演版.md](docs/5分钟精简路演版.md)
- GitHub 开源发布清单：[docs/GitHub开源发布清单.md](docs/GitHub开源发布清单.md)
- 完整规范：[SPEC.md](SPEC.md)
- 约束与设计规则：[AGENTS.md](AGENTS.md)

## 架构概览

```text
Telegram Bot API
    -> telegram_adapter
    -> command_router
       -> project_registry (YAML)
       -> task_store/state (SQLite)
       -> approval_service
       -> codex_runner
```

## 安全提示

- 若 Token 出现在日志或截图中，请立即轮换。
- 不要提交 `.env`、运行数据目录、含敏感信息的本地配置。
- 项目路径建议最小化授权到可信目录。

## 仓库结构

- 运行主目录：`mvp_scaffold/`
- 文档目录：`docs/`
- 配置样例：`env.example`, `projects.example.yaml`
- 数据库结构：`schema.sql`
