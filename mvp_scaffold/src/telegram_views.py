"""Telegram reply/view helpers for panels and common result markups."""

from dataclasses import dataclass
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from src.task_store import ScheduledTaskRecord, StatusSnapshot


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

    def approval_panel(self) -> TelegramReplySpec:
        return TelegramReplySpec(
            text="审批操作：",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(text="批准", callback_data="approval:approve"),
                        InlineKeyboardButton(text="拒绝", callback_data="approval:reject"),
                    ],
                    [
                        InlineKeyboardButton(text="批准+备注", callback_data="prompt:approve"),
                        InlineKeyboardButton(text="拒绝+原因", callback_data="prompt:reject"),
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
                        InlineKeyboardButton(text="精简模式", callback_data="cmd:ui_summary"),
                        InlineKeyboardButton(text="详细模式", callback_data="cmd:ui_verbose"),
                    ],
                    [
                        InlineKeyboardButton(text="查看根目录", callback_data="cmd:project_root_show"),
                        InlineKeyboardButton(text="设置根目录", callback_data="prompt:project_root"),
                    ],
                    [InlineKeyboardButton(text="清除输入引导", callback_data="prompt:clear")],
                ]
            ),
        )

    def status_result_markup(
        self,
        *,
        snapshot: StatusSnapshot,
        recent_projects: list[str] | None,
    ) -> InlineKeyboardMarkup | ReplyKeyboardMarkup:
        if snapshot.active_project_key is None:
            return self.project_shortcuts_markup(recent_projects)
        if snapshot.pending_approval:
            return InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(text="批准", callback_data="approval:approve"),
                        InlineKeyboardButton(text="拒绝", callback_data="approval:reject"),
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

    def default_result_markup(
        self, recent_projects: list[str] | None
    ) -> InlineKeyboardMarkup | ReplyKeyboardMarkup:
        if recent_projects:
            return self.project_shortcuts_markup(recent_projects)
        return self.main_menu_markup()
