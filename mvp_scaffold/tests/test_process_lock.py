from pathlib import Path

import pytest

from src.process_lock import acquire_process_lock


def test_acquire_process_lock_creates_and_releases_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "openfish.lock"

    lock = acquire_process_lock(lock_path)

    assert lock_path.exists() is True
    lock.release()
    assert lock_path.exists() is False


def test_acquire_process_lock_replaces_stale_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "openfish.lock"
    lock_path.write_text('{"pid": 999999, "started_at": 0}\n', encoding="utf-8")

    lock = acquire_process_lock(lock_path)

    assert lock_path.exists() is True
    contents = lock_path.read_text(encoding="utf-8")
    assert '"pid":' in contents
    assert "999999" not in contents
    lock.release()


def test_acquire_process_lock_rejects_live_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "openfish.lock"
    first_lock = acquire_process_lock(lock_path)

    with pytest.raises(RuntimeError, match="Another OpenFish instance is running"):
        acquire_process_lock(lock_path)

    first_lock.release()
