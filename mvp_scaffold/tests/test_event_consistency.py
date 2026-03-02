from pathlib import Path

from src.event_consistency import scan_event_consistency


def test_scan_event_consistency_detects_invalid_audit_action(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.py"
    bad_file.write_text(
        """
class X:
    def run(self):
        self.audit.log(action="not.allowed", message="x")
""".strip(),
        encoding="utf-8",
    )
    violations = scan_event_consistency([bad_file])
    assert violations
    assert "invalid audit action expression" in violations[0]


def test_scan_event_consistency_accepts_valid_patterns(tmp_path: Path) -> None:
    good_file = tmp_path / "good.py"
    good_file.write_text(
        """
from src import audit_events
class X:
    def run(self):
        self.audit.log(action=audit_events.TASK_CREATED, message="ok")
        self._insert_task_event(conn, 1, audit_events.TASK_STARTED, "ok")
""".strip(),
        encoding="utf-8",
    )
    violations = scan_event_consistency([good_file])
    assert violations == []
