"""Telegram-facing message builders used by the adapter."""

from pathlib import Path


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


def project_add_prompt(step: str, data: dict, default_root: Path | None) -> str:
    if step == "key":
        root_hint = f"\n默认根目录: {default_root}" if default_root else "\n当前未设置默认根目录。"
        return (
            "项目新增向导 1/4\n"
            "请输入项目 key。\n"
            "要求: 仅字母数字/._-，长度 1-64。"
            f"{root_hint}\n"
            "发送“取消”可退出。"
        )
    if step == "path":
        return "项目新增向导 2/4\n请输入项目绝对路径。\n如果要使用默认根目录，请回复“默认”。"
    if step == "name":
        return "项目新增向导 3/4\n请输入项目显示名称。\n如果要直接使用 key，请回复“跳过”。"

    path_text = data.get("path") or "默认根目录"
    name_text = data.get("name") or data.get("key") or "未设置"
    return (
        "项目新增向导 4/4\n"
        f"key: {data.get('key')}\n"
        f"路径: {path_text}\n"
        f"名称: {name_text}\n"
        "回复“确认”执行，回复“取消”放弃。"
    )


def schedule_add_prompt(step: str, data: dict) -> str:
    if step == "time":
        return "定时任务向导 1/4\n请输入执行时间，格式 HH:MM。\n例如: 09:30"
    if step == "mode":
        return "定时任务向导 2/4\n请输入任务类型：ask 或 do。"
    if step == "text":
        return "定时任务向导 3/4\n请输入定时任务内容。"
    return (
        "定时任务向导 4/4\n"
        f"时间: {data.get('hhmm')}\n"
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
