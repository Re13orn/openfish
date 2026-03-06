"""Simple single-process file lock with stale lock recovery."""

from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import time


@dataclass(slots=True)
class ProcessLock:
    path: Path

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return


def acquire_process_lock(lock_path: Path) -> ProcessLock:
    """Acquire an exclusive lock file, removing stale locks when safe."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write_lock_file(lock_path)
        return ProcessLock(path=lock_path)
    except FileExistsError as exc:
        existing = _read_lock_file(lock_path)
        existing_pid = existing.get("pid")
        if isinstance(existing_pid, int) and _is_pid_alive(existing_pid):
            raise RuntimeError(f"Another OpenFish instance is running (pid={existing_pid}).") from exc

        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass

        _write_lock_file(lock_path)
        return ProcessLock(path=lock_path)


def _write_lock_file(lock_path: Path) -> None:
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "started_at": int(time.time() * 1000),
            },
            ensure_ascii=True,
        )
        os.write(fd, (payload + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _read_lock_file(lock_path: Path) -> dict:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        if exc.errno == errno.EPERM:
            return True
        return False
