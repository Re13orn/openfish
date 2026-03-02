"""Build concise progress acknowledgements for long Telegram commands."""


class ProgressReporter:
    """Returns short phase-oriented progress hints by command."""

    _PHASES: dict[str, list[str]] = {
        "/do": ["校验项目", "启动 Codex", "汇总结果"],
        "/ask": ["校验项目", "加载上下文", "启动 Codex"],
        "/run": ["加载模板", "校验项目", "启动 Codex"],
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
