from src.models import CommandResult
from src.scheduler import ScheduledTaskService
from src.task_store import ScheduledTaskRecord


class TasksStub:
    def __init__(self) -> None:
        self.claimed = False
        self.recorded: list[tuple[int, int | None, str, str]] = []
        self.last_include_missed_before: bool | None = None

    def claim_due_scheduled_tasks(
        self,
        *,
        minute_of_day: int,
        trigger_date: str,
        include_missed_before: bool = False,
        limit: int = 10,
    ):  # noqa: ANN001
        _ = minute_of_day
        _ = trigger_date
        _ = limit
        self.last_include_missed_before = include_missed_before
        if self.claimed:
            return []
        self.claimed = True
        return [
            ScheduledTaskRecord(
                id=1,
                user_id=1,
                project_id=2,
                telegram_chat_id="chat-1",
                command_type="ask",
                request_text="daily check",
                minute_of_day=0,
                enabled=True,
                last_triggered_on=None,
                last_task_id=None,
                last_run_status=None,
                last_run_summary=None,
            )
        ]

    def record_scheduled_task_run(
        self,
        *,
        schedule_id: int,
        task_id: int | None,
        status: str,
        summary: str,
    ) -> None:
        self.recorded.append((schedule_id, task_id, status, summary))


class RouterStub:
    def run_scheduled_task(self, schedule):  # noqa: ANN001
        _ = schedule
        return CommandResult("ok", metadata={"task_id": 7, "status": "completed"})


def test_scheduler_poll_once_records_result() -> None:
    tasks = TasksStub()
    router = RouterStub()
    service = ScheduledTaskService(tasks=tasks, router=router, poll_interval_seconds=20, enabled=True)

    service.poll_once()
    service.poll_once()

    assert len(tasks.recorded) == 1
    schedule_id, task_id, status, summary = tasks.recorded[0]
    assert schedule_id == 1
    assert task_id == 7
    assert status == "completed"
    assert summary == "ok"


def test_scheduler_uses_catchup_policy_flag() -> None:
    tasks = TasksStub()
    router = RouterStub()
    service = ScheduledTaskService(
        tasks=tasks,
        router=router,
        poll_interval_seconds=20,
        enabled=True,
        missed_run_policy="catchup_once",
    )

    service.poll_once()

    assert tasks.last_include_missed_before is True
