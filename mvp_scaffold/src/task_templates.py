"""Built-in task templates for faster command usage."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskTemplate:
    key: str
    title: str
    mode: str  # "ask" | "do"
    instruction: str


BUILTIN_TEMPLATES: dict[str, TaskTemplate] = {
    "quick-audit": TaskTemplate(
        key="quick-audit",
        title="快速风险审计",
        mode="ask",
        instruction=(
            "请对当前项目做快速风险审计（只读，不修改文件）。"
            "请忽略目录 .codex_telegram_uploads，不要将其作为隐藏目录风险。\n"
            "输出："
            "1) 关键风险 2) 风险等级 3) 最小改进建议。"
        ),
    ),
    "bug-locate": TaskTemplate(
        key="bug-locate",
        title="问题定位",
        mode="ask",
        instruction=(
            "请定位问题根因并给出最小修复方案（先分析，不改代码）。"
        ),
    ),
    "minimal-fix": TaskTemplate(
        key="minimal-fix",
        title="最小修复执行",
        mode="do",
        instruction=(
            "请实施最小修复，限制改动范围，并在结束时总结改动与测试结果。"
        ),
    ),
}
