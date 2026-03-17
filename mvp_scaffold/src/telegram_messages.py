"""Telegram-facing message builders used by the adapter."""

from pathlib import Path

from src.models import ProjectTemplatePreset


def internal_request_error() -> str:
    return "Internal error while handling request."


def upload_processing_ack() -> str:
    return "已收到文件，处理中：\n- 校验项目\n- 校验文件\n- 下载并分析"


def upload_oversized_hint() -> str:
    return "文件过大，Telegram 无法下载该文件。请压缩后重试，或拆分后再上传。\n可用 /upload_policy 查看本地上传限制。"


def upload_bad_request(error_text: str) -> str:
    return f"上传失败：{error_text}"


def upload_internal_error() -> str:
    return "Internal error while handling uploaded file."


def callback_error() -> str:
    return "处理按钮操作时发生错误。"


def unknown_callback() -> str:
    return "未识别的按钮操作，请重试。"


def prompt_mode_cleared() -> str:
    return "已清除输入引导。"


def unknown_prompt_mode() -> str:
    return "未识别的输入模式。"


def prompt_mode_hint(hint: str) -> str:
    return f"{hint}\n可发送 /help 查看命令。"


def local_file_send_failed(error_text: str) -> str:
    return f"下载本机文件失败：{error_text}"


def wizard_missing_state() -> str:
    return "当前向导已结束，按钮可能已过期。请重新开始。"


def wizard_cancelled() -> str:
    return "已取消当前向导。"


def wizard_unknown_button() -> str:
    return "未识别的向导按钮，可能已过期。"


def wizard_project_requirement() -> str:
    return "请先选择项目，再使用这个向导。"


def project_add_prompt(
    step: str,
    data: dict,
    default_root: Path | None,
    templates: list[ProjectTemplatePreset] | None = None,
) -> str:
    template_map = {preset.key: preset for preset in (templates or [])}
    if step == "key":
        root_hint = f"\n默认根目录: {default_root}" if default_root else "\n当前未设置默认根目录。"
        return (
            "项目新增向导 1/7\n"
            "请输入项目 key。\n"
            "要求: 仅字母数字/._-，长度 1-64。"
            f"{root_hint}\n"
            "发送“取消”可退出。"
        )
    if step == "path":
        return "项目新增向导 2/7\n请输入项目绝对路径。\n如果要使用默认根目录，请回复“默认”。"
    if step == "template":
        if templates:
            template_lines = [f"- {preset.key}: {preset.description or preset.name}" for preset in templates[:8]]
            return (
                "项目新增向导 3/7\n"
                "请选择项目模板。\n"
                "可直接输入模板 key，或点下面按钮；如果不需要模板，请回复“跳过”。\n"
                + "\n".join(template_lines)
            )
        return (
            "项目新增向导 3/7\n"
            "当前没有可用项目模板。\n"
            "请回复“跳过”，或先设置 /project-template-root。"
        )
    if step == "mode":
        return (
            "项目新增向导 4/7\n"
            "请选择项目模式：\n"
            "- normal: 普通项目，仅创建并切换\n"
            "- autopilot: 创建后自动启动长期任务"
        )
    if step == "goal":
        return "项目新增向导 5/7\n请输入 Autopilot 目标。\n创建完成后会立即按这个目标启动。"
    if step == "name":
        return "项目新增向导 6/7\n请输入项目显示名称。\n如果要直接使用 key，请回复“跳过”。"

    path_text = data.get("path") or "默认根目录"
    name_text = data.get("name") or data.get("key") or "未设置"
    source_repo = data.get("source_repo")
    template_name = data.get("template_name") or "未使用"
    if template_name in template_map:
        template_name = f"{template_name} ({template_map[template_name].name})"
    mode_text = data.get("default_run_mode") or "normal"
    goal_text = data.get("autopilot_goal") or "未设置"
    bootstrap_text = "未设置"
    template_key = data.get("template_name")
    if template_key in template_map:
        bootstrap_text = (
            template_map[template_key].default_autopilot_bootstrap_instruction or "未设置"
        )
    return (
        "项目新增向导 7/7\n"
        f"key: {data.get('key')}\n"
        f"路径: {path_text}\n"
        f"模板: {template_name}\n"
        f"模式: {mode_text}\n"
        f"Autopilot 目标: {goal_text}\n"
        f"首轮启动: {bootstrap_text}\n"
        f"名称: {name_text}\n"
        f"源仓库: {source_repo or '未设置'}\n"
        "回复“确认”执行，回复“取消”放弃。"
    )


def schedule_add_prompt(step: str, data: dict) -> str:
    trigger_text = (
        f"每隔 {data.get('interval_minutes')} 分钟"
        if data.get("schedule_type") == "interval"
        else data.get("hhmm")
    )
    if step == "trigger":
        return "定时任务向导 1/5\n请选择触发方式：每天定时，或每隔一段时间。"
    if step == "time":
        return "定时任务向导 2/5\n请输入每日执行时间，格式 HH:MM。\n例如: 09:30"
    if step == "interval":
        return "定时任务向导 2/5\n请输入间隔，例如 30m、2h、30分钟、2小时。"
    if step == "mode":
        return "定时任务向导 3/5\n请输入任务类型：ask 或 do。"
    if step == "text":
        return "定时任务向导 4/5\n请输入定时任务内容。"
    return (
        "定时任务向导 5/5\n"
        f"触发: {trigger_text}\n"
        f"类型: {data.get('mode')}\n"
        f"内容: {data.get('text')}\n"
        "回复“确认”执行，回复“取消”放弃。"
    )


def approval_note_prompt(step: str, data: dict, *, action: str) -> str:
    label = "批准" if action == "approve" else "拒绝"
    subject = "审批备注" if action == "approve" else "拒绝原因"
    if step == "note":
        task_summary = data.get("task_summary") or "待审批任务"
        return (
            f"{label}向导 1/2\n"
            f"任务: {task_summary}\n"
            f"请输入{subject}，或直接点下面的快捷按钮。"
        )
    note_text = data.get("note") or ("无备注" if action == "approve" else "用户拒绝")
    return (
        f"{label}向导 2/2\n"
        f"审批: #{data.get('approval_id')}\n"
        f"{subject}: {note_text}\n"
        "回复“确认”执行，回复“取消”放弃。"
    )
