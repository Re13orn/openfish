"""Background scheduler for periodic Telegram/Codex tasks."""

from __future__ import annotations

from datetime import datetime
import logging
from threading import Event, Thread


logger = logging.getLogger(__name__)


class ScheduledTaskService:
    """Polls due periodic tasks and triggers execution through CommandRouter."""

    def __init__(
        self,
        *,
        tasks,
        router,
        poll_interval_seconds: int,
        enabled: bool = True,
        missed_run_policy: str = "skip",
    ) -> None:  # noqa: ANN001
        self.tasks = tasks
        self.router = router
        self.poll_interval_seconds = max(5, poll_interval_seconds)
        self.enabled = enabled
        self.missed_run_policy = missed_run_policy if missed_run_policy in {"skip", "catchup_once"} else "skip"
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._restart_count: int = 0
        self._queue_health_alert = getattr(tasks, "queue_health_alert_all_chats", None)

    @property
    def restart_count(self) -> int:
        return self._restart_count

    def start(self) -> None:
        if not self.enabled:
            logger.info("Scheduled task service disabled by configuration.")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._supervised_loop, name="scheduled-task-service", daemon=True)
        self._thread.start()
        logger.info("Scheduled task service started.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("Scheduled task service stopped.")

    def is_alive(self) -> bool:
        """Return True if the background polling thread is running."""
        if not self.enabled:
            return True  # disabled intentionally, not a fault
        return self._thread is not None and self._thread.is_alive()

    def _supervised_loop(self) -> None:
        """Outer watchdog: restarts _run_loop if it exits unexpectedly."""
        while not self._stop_event.is_set():
            try:
                self._run_loop()
            except Exception:
                logger.exception("Scheduler _run_loop crashed unexpectedly.")
            if self._stop_event.is_set():
                break
            self._restart_count += 1
            logger.warning(
                "Scheduler thread restarting (restart #%d).",
                self._restart_count,
            )
            try:
                if callable(self._queue_health_alert):
                    self._queue_health_alert(
                        message=f"调度器意外崩溃并已自动重启（第 {self._restart_count} 次），请检查日志。",
                        kind="scheduler_restarted",
                    )
            except Exception:
                logger.warning("Failed to queue scheduler restart health alert.", exc_info=True)
            self._stop_event.wait(5)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self.poll_interval_seconds)

    def poll_once(self) -> None:
        now = datetime.now()
        minute_of_day = now.hour * 60 + now.minute
        trigger_date = now.date().isoformat()

        due = self.tasks.claim_due_scheduled_tasks(
            minute_of_day=minute_of_day,
            trigger_date=trigger_date,
            include_missed_before=self.missed_run_policy == "catchup_once",
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
