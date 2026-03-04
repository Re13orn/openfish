from src.progress_reporter import ProgressReporter


def test_progress_ack_for_long_command() -> None:
    reporter = ProgressReporter()
    text = reporter.ack_text("/do")
    assert text is not None
    assert "校验项目" in text
    assert "启动 Codex" in text


def test_progress_ack_for_unknown_command() -> None:
    reporter = ProgressReporter()
    assert reporter.ack_text("/unknown") is None


def test_progress_ack_for_retry() -> None:
    reporter = ProgressReporter()
    text = reporter.ack_text("/retry")
    assert text is not None
    assert "读取最近任务" in text


def test_progress_ack_for_skill_install() -> None:
    reporter = ProgressReporter()
    text = reporter.ack_text("/skill-install")
    assert text is not None
    assert "安装 Skill" in text


def test_progress_ack_for_schedule_add() -> None:
    reporter = ProgressReporter()
    text = reporter.ack_text("/schedule-add")
    assert text is not None
    assert "写入定期任务" in text
