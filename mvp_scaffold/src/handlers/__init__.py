"""Command handler mixins for CommandRouter."""

from src.handlers.autopilot_handler import _AutopilotHandler
from src.handlers.project_handler import _ProjectHandler
from src.handlers.schedule_handler import _ScheduleHandler
from src.handlers.skills_handler import _SkillsHandler
from src.handlers.system_handler import _SystemHandler
from src.handlers.task_handler import _TaskHandler

__all__ = [
    "_AutopilotHandler",
    "_ProjectHandler",
    "_ScheduleHandler",
    "_SkillsHandler",
    "_SystemHandler",
    "_TaskHandler",
]
