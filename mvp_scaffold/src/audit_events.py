"""Canonical audit event codes."""

PROJECT_SELECTED = "project.selected"
PROJECT_ROOT_UPDATED = "project.root_updated"
PROJECT_ADDED = "project.added"
PROJECT_DISABLED = "project.disabled"
PROJECT_ARCHIVED = "project.archived"
TASK_CREATED = "task.created"
TASK_STARTED = "task.started"
TASK_WAITING_APPROVAL = "task.waiting_approval"
TASK_APPROVAL_RESUMED = "task.approval_resumed"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
TASK_CANCELLED = "task.cancelled"
TASK_REJECTED = "task.rejected"
TASK_UNKNOWN_EVENT = "task.unknown_event"
TASK_LAST_VIEWED = "task.last_viewed"
TASK_RETRIED = "task.retried"

APPROVAL_REQUESTED = "approval.requested"
APPROVAL_GRANTED = "approval.granted"
APPROVAL_REJECTED = "approval.rejected"

MEMORY_VIEWED = "memory.viewed"
NOTE_ADDED = "memory.note_added"
DIFF_VIEWED = "repo.diff_viewed"
UPLOAD_RECEIVED = "upload.received"
UPLOAD_REJECTED = "upload.rejected"
UPLOAD_POLICY_VIEWED = "upload.policy_viewed"
START_VIEWED = "ui.start_viewed"
TASK_QUEUE_BLOCKED = "task.queue_blocked"
TEMPLATES_VIEWED = "template.viewed"
TEMPLATE_RUN = "template.run"
SKILLS_VIEWED = "skill.viewed"
SKILL_INSTALL_REQUESTED = "skill.install_requested"
SKILL_INSTALLED = "skill.installed"
SKILL_INSTALL_FAILED = "skill.install_failed"
MCP_VIEWED = "mcp.viewed"
MCP_UPDATED = "mcp.updated"
SESSIONS_VIEWED = "session.viewed_all"
SESSION_VIEWED = "session.viewed_one"
SESSION_IMPORTED = "session.imported"
SYSTEM_VERSION_VIEWED = "system.version_viewed"
SYSTEM_UPDATE_CHECKED = "system.update_checked"
SYSTEM_UPDATE_TRIGGERED = "system.update_triggered"
SYSTEM_RESTART_TRIGGERED = "system.restart_triggered"
SYSTEM_LOGS_VIEWED = "system.logs_viewed"
SYSTEM_LOGS_CLEARED = "system.logs_cleared"
SYSTEM_LOCAL_FILE_SENT = "system.local_file_sent"
SCHEDULE_CREATED = "schedule.created"
SCHEDULE_VIEWED = "schedule.viewed"
SCHEDULE_DELETED = "schedule.deleted"
SCHEDULE_TOGGLED = "schedule.toggled"
SCHEDULE_MANUAL_RUN = "schedule.manual_run"
SCHEDULE_TRIGGERED = "schedule.triggered"
SCHEDULE_FAILED = "schedule.failed"

UNKNOWN_EVENT = "system.unknown_event"


ALL_EVENTS = {
    PROJECT_SELECTED,
    PROJECT_ROOT_UPDATED,
    PROJECT_ADDED,
    PROJECT_DISABLED,
    PROJECT_ARCHIVED,
    TASK_CREATED,
    TASK_STARTED,
    TASK_WAITING_APPROVAL,
    TASK_APPROVAL_RESUMED,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_CANCELLED,
    TASK_REJECTED,
    TASK_UNKNOWN_EVENT,
    TASK_LAST_VIEWED,
    TASK_RETRIED,
    APPROVAL_REQUESTED,
    APPROVAL_GRANTED,
    APPROVAL_REJECTED,
    MEMORY_VIEWED,
    NOTE_ADDED,
    DIFF_VIEWED,
    UPLOAD_RECEIVED,
    UPLOAD_REJECTED,
    UPLOAD_POLICY_VIEWED,
    START_VIEWED,
    TASK_QUEUE_BLOCKED,
    TEMPLATES_VIEWED,
    TEMPLATE_RUN,
    SKILLS_VIEWED,
    SKILL_INSTALL_REQUESTED,
    SKILL_INSTALLED,
    SKILL_INSTALL_FAILED,
    MCP_VIEWED,
    MCP_UPDATED,
    SESSIONS_VIEWED,
    SESSION_VIEWED,
    SESSION_IMPORTED,
    SYSTEM_VERSION_VIEWED,
    SYSTEM_UPDATE_CHECKED,
    SYSTEM_UPDATE_TRIGGERED,
    SYSTEM_RESTART_TRIGGERED,
    SYSTEM_LOGS_VIEWED,
    SYSTEM_LOGS_CLEARED,
    SYSTEM_LOCAL_FILE_SENT,
    SCHEDULE_CREATED,
    SCHEDULE_VIEWED,
    SCHEDULE_DELETED,
    SCHEDULE_TOGGLED,
    SCHEDULE_MANUAL_RUN,
    SCHEDULE_TRIGGERED,
    SCHEDULE_FAILED,
    UNKNOWN_EVENT,
}

TASK_EVENT_TYPES = {
    TASK_CREATED,
    TASK_STARTED,
    TASK_WAITING_APPROVAL,
    TASK_APPROVAL_RESUMED,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_CANCELLED,
    TASK_REJECTED,
    TASK_UNKNOWN_EVENT,
    APPROVAL_REQUESTED,
}
