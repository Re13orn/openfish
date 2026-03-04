"""Background scheduler for periodic Telegram/Codex tasks."""

from __future__ import annotations

from datetime import datetime
import logging
from threading import Event, Thread


logger = logging.getLogger(__name__)


class ScheduledTaskService:
    """Polls due periodic tasks and triggers execution through CommandRouter."""

    def __init__(self, *, tasks, router, poll_interval_seconds: int, enabled: bool = True) -> None:  # noqa: ANN001
        self.tasks = tasks
        self.router = router
        self.poll_interval_seconds = max(5, poll_interval_seconds)
        self.enabled = enabled
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            logger.info("Scheduled task service disabled by configuration.")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._run_loop, name="scheduled-task-service", daemon=True)
        self._thread.start()
        logger.info("Scheduled task service started.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("Scheduled task service stopped.")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("Unexpected scheduler poll failure.")
            self._stop_event.wait(self.poll_interval_seconds)

    def poll_once(self) -> None:
        now = datetime.now()
        minute_of_day = now.hour * 60 + now.minute
        trigger_date = now.date().isoformat()

        due = self.tasks.claim_due_scheduled_tasks(
            minute_of_day=minute_of_day,
            trigger_date=trigger_date,
        )
        for item in due:
            try:
                result = self.router.run_scheduled_task(item)
                metadata = result.metadata or {}
                task_id_raw = metadata.get("task_id")
                task_id = None
                if isinstance(task_id_raw, int):
                    task_id = task_id_raw
                elif isinstance(task_id_raw, str) and task_id_raw.isdigit():
                    task_id = int(task_id_raw)
                status = str(metadata.get("status") or "unknown")
                self.tasks.record_scheduled_task_run(
                    schedule_id=item.id,
                    task_id=task_id,
                    status=status,
                    summary=result.reply_text,
                )
            except Exception as exc:
                logger.exception("Scheduled task execution failed: schedule_id=%s", item.id)
                self.tasks.record_scheduled_task_run(
                    schedule_id=item.id,
                    task_id=None,
                    status="failed",
                    summary=f"定期任务执行异常: {exc}",
                )
