"""Telegram reply/view helpers for panels and common result markups."""

from dataclasses import dataclass
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from src.codex_session_service import CodexSessionRecord
from src.task_store import ScheduledTaskRecord, StatusSnapshot, TaskRecord


@dataclass(slots=True)
class TelegramReplySpec:
    text: str
    reply_markup: Any | None = None


class TelegramViewFactory:
    """Builds Telegram reply markups independent from command execution logic."""

    MENU_PROJECTS = "项目"
    MENU_ASK = "提问"
    MENU_DO = "执行"
    MENU_STATUS = "状态"
    MENU_RESUME = "继续"
    MENU_DIFF = "变更"
    MENU_SCHEDULE = "定时"
    MENU_MORE = "更多"
    MENU_HELP = "帮助"

    def main_menu_markup(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [self.MENU_PROJECTS, self.MENU_ASK, self.MENU_DO],
                [self.MENU_STATUS, self.MENU_RESUME, self.MENU_DIFF],
                [self.MENU_SCHEDULE, self.MENU_MORE, self.MENU_HELP],
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
            selective=False,
        )

    def project_shortcuts_markup(
        self, recent_projects: list[str] | None
    ) -> InlineKeyboardMarkup | ReplyKeyboardMarkup:
        keys = [key for key in (recent_projects or []) if key]
        if not keys:
            return InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(text="项目面板", callback_data="panel:projects")],
                    [InlineKeyboardButton(text="新增项目", callback_data="prompt:project_add")],
                ]
            )

        rows: list[list[InlineKeyboardButton]] = []
        current_row: list[InlineKeyboardButton] = []
        for key in keys[:6]:
            current_row.append(InlineKeyboardButton(text=key, callback_data=f"use:{key}"))
            if len(current_row) == 2:
                rows.append(current_row)
                current_row = []
        if current_row:
            rows.append(current_row)
        rows.append(
            [
                InlineKeyboardButton(text="项目面板", callback_data="panel:projects"),
                InlineKeyboardButton(text="新增项目", callback_data="prompt:project_add"),
            ]
        )
        return InlineKeyboardMarkup(rows)

    def projects_panel(
        self,
        *,
        active_key: str | None,
        recent_keys: list[str],
        ordered_keys: list[str],
    ) -> TelegramReplySpec:
        rows: list[list[InlineKeyboardButton]] = []
        for key in ordered_keys[:10]:
            label = f"当前: {key}" if key == active_key else f"切换: {key}"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"use:{key}")])
        rows.extend(
            [
                [InlineKeyboardButton(text="查看项目列表", callback_data="cmd:projects")],
                [
                    InlineKeyboardButton(text="新增项目", callback_data="prompt:project_add"),
                    InlineKeyboardButton(text="设置默认根目录", callback_data="prompt:project_root"),
                ],
                [
                    InlineKeyboardButton(text="查看默认根目录", callback_data="cmd:project_root_show"),
                    InlineKeyboardButton(text="手输切换", callback_data="prompt:use"),
                ],
                [
                    InlineKeyboardButton(text="停用当前", callback_data="cmd:project_disable_current"),
                    InlineKeyboardButton(text="归档当前", callback_data="cmd:project_archive_current"),
                ],
                [
                    InlineKeyboardButton(text="停用指定", callback_data="prompt:project_disable"),
                    InlineKeyboardButton(text="归档指定", callback_data="prompt:project_archive"),
                ],
                [InlineKeyboardButton(text="更多操作", callback_data="panel:more")],
            ]
        )
        lines = ["项目操作："]
        if active_key:
            lines.append(f"当前项目: {active_key}")
        if recent_keys:
            lines.append("最近项目优先展示，可直接点选切换。")
        return TelegramReplySpec(
            text="\n".join(lines),
            reply_markup=InlineKeyboardMarkup(rows),
        )

    def approval_panel(self, *, approval_id: int | None = None) -> TelegramReplySpec:
        approve_callback = "approval:approve"
        reject_callback = "approval:reject"
        approve_prompt_callback = "prompt:approve"
        reject_prompt_callback = "prompt:reject"
        if approval_id is not None:
            approve_callback = f"approval:approve:{approval_id}"
            reject_callback = f"approval:reject:{approval_id}"
            approve_prompt_callback = f"prompt:approve:{approval_id}"
            reject_prompt_callback = f"prompt:reject:{approval_id}"
        return TelegramReplySpec(
            text="审批操作：",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(text="批准", callback_data=approve_callback),
                        InlineKeyboardButton(text="拒绝", callback_data=reject_callback),
                    ],
                    [
                        InlineKeyboardButton(text="批准+备注", callback_data=approve_prompt_callback),
                        InlineKeyboardButton(text="拒绝+原因", callback_data=reject_prompt_callback),
                    ],
                    [InlineKeyboardButton(text="查看状态", callback_data="approval:status")],
                ]
            ),
        )

    def more_panel(self) -> TelegramReplySpec:
        return TelegramReplySpec(
            text="更多操作：",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(text="最近任务", callback_data="cmd:last"),
                        InlineKeyboardButton(text="项目记忆", callback_data="cmd:memory"),
                    ],
                    [
                        InlineKeyboardButton(text="模板列表", callback_data="cmd:templates"),
                        InlineKeyboardButton(text="执行模板", callback_data="prompt:run"),
                    ],
                    [
                        InlineKeyboardButton(text="审批面板", callback_data="panel:approval"),
                        InlineKeyboardButton(text="上传策略", callback_data="cmd:upload_policy"),
                    ],
                    [
                        InlineKeyboardButton(text="Skills", callback_data="cmd:skills"),
                        InlineKeyboardButton(text="安装 Skill", callback_data="prompt:skill_install"),
                    ],
                    [
                        InlineKeyboardButton(text="MCP 列表", callback_data="cmd:mcp"),
                        InlineKeyboardButton(text="MCP 详情", callback_data="prompt:mcp"),
                    ],
                    [
                        InlineKeyboardButton(text="会话", callback_data="cmd:sessions"),
                        InlineKeyboardButton(text="任务", callback_data="cmd:tasks"),
                    ],
                    [InlineKeyboardButton(text="清空历史任务", callback_data="cmd:tasks_clear")],
                    [InlineKeyboardButton(text="模型", callback_data="panel:model")],
                    [
                        InlineKeyboardButton(text="版本", callback_data="cmd:version"),
                        InlineKeyboardButton(text="检查更新", callback_data="cmd:update_check"),
                        InlineKeyboardButton(text="立即更新", callback_data="cmd:update"),
                    ],
                    [
                        InlineKeyboardButton(text="重启服务", callback_data="cmd:restart"),
                        InlineKeyboardButton(text="查看日志", callback_data="cmd:logs"),
                        InlineKeyboardButton(text="清空日志", callback_data="cmd:logs_clear"),
                    ],
                    [
                        InlineKeyboardButton(text="精简模式", callback_data="cmd:ui_summary"),
                        InlineKeyboardButton(text="详细模式", callback_data="cmd:ui_verbose"),
                        InlineKeyboardButton(text="过程流模式", callback_data="cmd:ui_stream"),
                    ],
                    [
                        InlineKeyboardButton(text="查看根目录", callback_data="cmd:project_root_show"),
                        InlineKeyboardButton(text="设置根目录", callback_data="prompt:project_root"),
                    ],
                    [InlineKeyboardButton(text="清除输入引导", callback_data="prompt:clear")],
                ]
            ),
        )

    def model_panel(
        self,
        *,
        current_model: str | None,
        model_choices: list[str],
    ) -> TelegramReplySpec:
        rows: list[list[InlineKeyboardButton]] = []
        current_row: list[InlineKeyboardButton] = []
        for model in model_choices[:8]:
            label = f"当前: {model}" if current_model == model else model
            current_row.append(InlineKeyboardButton(text=label, callback_data=f"model:set:{model}"))
            if len(current_row) == 2:
                rows.append(current_row)
                current_row = []
        if current_row:
            rows.append(current_row)
        rows.extend(
            [
                [InlineKeyboardButton(text="手动输入模型", callback_data="prompt:model")],
                [InlineKeyboardButton(text="恢复默认", callback_data="model:reset")],
                [InlineKeyboardButton(text="查看当前模型", callback_data="cmd:model")],
            ]
        )
        current = current_model or "默认（跟随 Codex 配置）"
        return TelegramReplySpec(
            text=f"模型设置：\n当前: {current}",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    def memory_pagination_markup(
        self,
        *,
        page: int,
        total_pages: int,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        page_buttons: list[InlineKeyboardButton] = []
        if page > 1:
            page_buttons.append(InlineKeyboardButton(text="上一页", callback_data=f"memory:page:{page - 1}"))
        if page < total_pages:
            page_buttons.append(InlineKeyboardButton(text="下一页", callback_data=f"memory:page:{page + 1}"))
        if page_buttons:
            rows.append(page_buttons)
        rows.append([InlineKeyboardButton(text="更多操作", callback_data="panel:more")])
        return InlineKeyboardMarkup(rows)

    def mcp_detail_markup(self, *, name: str, enabled: bool) -> InlineKeyboardMarkup:
        toggle_callback = f"mcp:{'disable' if enabled else 'enable'}:{name}"
        toggle_text = "停用 MCP" if enabled else "启用 MCP"
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(text=toggle_text, callback_data=toggle_callback)],
                [
                    InlineKeyboardButton(text="刷新详情", callback_data=f"cmd:mcp_detail:{name}"),
                    InlineKeyboardButton(text="返回列表", callback_data="cmd:mcp"),
                ],
            ]
        )

    def sessions_list_markup(
        self,
        *,
        sessions: list[CodexSessionRecord],
        page: int,
        total_pages: int,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for item in sessions[:6]:
            source = "OF" if item.source == "openfish" else "本机"
            label = f"{source} {item.session_id[:8]}"
            rows.append(
                [InlineKeyboardButton(text=label, callback_data=f"cmd:session_detail:{item.session_id}")]
            )
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="上一页", callback_data=f"sessions:page:{page - 1}"))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="下一页", callback_data=f"sessions:page:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton(text="清空历史任务", callback_data="cmd:tasks_clear")])
        rows.append([InlineKeyboardButton(text="更多操作", callback_data="panel:more")])
        return InlineKeyboardMarkup(rows)

    def session_detail_markup(self, *, record: CodexSessionRecord) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if record.importable:
            rows.append(
                [InlineKeyboardButton(text="导入为项目并继续", callback_data=f"session:import:{record.session_id}")]
            )
        elif record.project_key:
            rows.append([InlineKeyboardButton(text="切换到项目", callback_data=f"use:{record.project_key}")])
        rows.append(
            [
                InlineKeyboardButton(text="返回会话列表", callback_data="cmd:sessions"),
                InlineKeyboardButton(text="更多操作", callback_data="panel:more"),
            ]
        )
        return InlineKeyboardMarkup(rows)

    def status_result_markup(
        self,
        *,
        snapshot: StatusSnapshot,
        recent_projects: list[str] | None,
    ) -> InlineKeyboardMarkup | ReplyKeyboardMarkup:
        if snapshot.active_project_key is None:
            return self.project_shortcuts_markup(recent_projects)
        if snapshot.pending_approval:
            if snapshot.pending_approval_id is not None:
                approve_callback = f"approval:approve:{snapshot.pending_approval_id}"
                reject_callback = f"approval:reject:{snapshot.pending_approval_id}"
            else:
                approve_callback = "panel:approval"
                reject_callback = "panel:approval"
            return InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(text="批准", callback_data=approve_callback),
                        InlineKeyboardButton(text="拒绝", callback_data=reject_callback),
                    ],
                    [
                        InlineKeyboardButton(text="看变更", callback_data="status:diff"),
                        InlineKeyboardButton(text="定时", callback_data="status:schedule"),
                    ],
                    [
                        InlineKeyboardButton(text="项目", callback_data="status:projects"),
                        InlineKeyboardButton(text="更多", callback_data="status:more"),
                    ],
                ]
            )
        if snapshot.most_recent_task_summary:
            return InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(text="继续", callback_data="status:resume"),
                        InlineKeyboardButton(text="看变更", callback_data="status:diff"),
                    ],
                    [
                        InlineKeyboardButton(text="最近任务", callback_data="cmd:last"),
                        InlineKeyboardButton(text="定时", callback_data="status:schedule"),
                    ],
                    [
                        InlineKeyboardButton(text="项目", callback_data="status:projects"),
                        InlineKeyboardButton(text="更多", callback_data="status:more"),
                    ],
                ]
            )
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(text="提问", callback_data="status:ask"),
                    InlineKeyboardButton(text="执行", callback_data="status:do"),
                ],
                [
                    InlineKeyboardButton(text="项目", callback_data="status:projects"),
                    InlineKeyboardButton(text="定时", callback_data="status:schedule"),
                ],
                [
                    InlineKeyboardButton(text="最近任务", callback_data="cmd:last"),
                    InlineKeyboardButton(text="更多", callback_data="status:more"),
                ],
            ]
        )

    def schedule_list_markup(
        self, schedules: list[ScheduledTaskRecord]
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text="新增定时", callback_data="prompt:schedule_add")]
        ]
        for item in schedules[:8]:
            rows.append(
                [
                    InlineKeyboardButton(text=f"运行 #{item.id}", callback_data=f"schedule:run:{item.id}"),
                    InlineKeyboardButton(
                        text=("暂停" if item.enabled else "启用"),
                        callback_data=f"schedule:{'pause' if item.enabled else 'enable'}:{item.id}",
                    ),
                    InlineKeyboardButton(text="删除", callback_data=f"schedule:del:{item.id}"),
                ]
            )
        rows.append([InlineKeyboardButton(text="刷新", callback_data="schedule:refresh")])
        return InlineKeyboardMarkup(rows)

    def tasks_list_markup(self, tasks: list[TaskRecord], *, page: int, total_pages: int) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for item in tasks[:8]:
            action = (
                InlineKeyboardButton(text=f"取消 #{item.id}", callback_data=f"task:cancel:{item.id}")
                if item.status in {"created", "running", "waiting_approval"}
                else InlineKeyboardButton(text=f"删除 #{item.id}", callback_data=f"task:delete:{item.id}")
            )
            rows.append([action])
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="上一页", callback_data=f"tasks:page:{page - 1}"))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="下一页", callback_data=f"tasks:page:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton(text="清空历史任务", callback_data="cmd:tasks_clear")])
        rows.append([InlineKeyboardButton(text="更多操作", callback_data="panel:more")])
        return InlineKeyboardMarkup(rows)

    def default_result_markup(
        self, recent_projects: list[str] | None
    ) -> InlineKeyboardMarkup | ReplyKeyboardMarkup:
        if recent_projects:
            return self.project_shortcuts_markup(recent_projects)
        return self.main_menu_markup()
