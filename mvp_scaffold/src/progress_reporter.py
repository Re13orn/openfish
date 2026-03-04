"""Build concise progress acknowledgements for long Telegram commands."""


class ProgressReporter:
    """Returns short phase-oriented progress hints by command."""

    _PHASES: dict[str, list[str]] = {
        "/do": ["校验项目", "启动 Codex", "汇总结果"],
        "/ask": ["校验项目", "加载上下文", "启动 Codex"],
        "/project-root": ["校验目录", "写入默认项目根目录"],
        "/project-add": ["校验参数", "写入项目注册表", "同步状态"],
        "/project-disable": ["校验项目", "标记停用", "同步状态"],
        "/project-archive": ["校验项目", "归档并停用", "同步状态"],
        "/run": ["加载模板", "校验项目", "启动 Codex"],
        "/skill-install": ["校验来源", "安装 Skill", "汇总结果"],
        "/schedule-add": ["校验项目", "写入定期任务", "返回配置摘要"],
        "/schedule-list": ["读取项目定期任务", "汇总结果"],
        "/schedule-run": ["读取定期任务", "触发执行", "汇总结果"],
        "/schedule-pause": ["读取定期任务", "更新为暂停"],
        "/schedule-enable": ["读取定期任务", "更新为启用"],
        "/schedule-del": ["校验项目", "删除定期任务"],
        "/retry": ["读取最近任务", "校验项目", "启动 Codex"],
        "/resume": ["定位可恢复任务", "继续 Codex 会话", "汇总状态"],
        "/approve": ["确认待审批任务", "继续执行", "汇总结果"],
        "/reject": ["确认待审批任务", "写入拒绝结果"],
        "/diff": ["读取仓库状态", "生成差异摘要"],
        "/memory": ["读取项目记忆", "生成摘要"],
    }

    def ack_text(self, command: str) -> str | None:
        phases = self._PHASES.get(command)
        if not phases:
            return None
        lines = "\n".join(f"- {item}" for item in phases)
        return f"已收到，处理中：\n{lines}"
