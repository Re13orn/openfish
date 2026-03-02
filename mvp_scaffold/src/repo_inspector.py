"""Read-only Git repository inspection helpers."""

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(slots=True)
class RepoState:
    is_git_repo: bool
    branch: str | None
    dirty: bool | None


class RepoInspector:
    """Provides lightweight repository status and diff summaries."""

    def __init__(self, timeout_seconds: int = 10) -> None:
        self.timeout_seconds = timeout_seconds

    def inspect(self, project_path: Path) -> RepoState:
        if not self._is_git_repo(project_path):
            return RepoState(is_git_repo=False, branch=None, dirty=None)

        branch = self._run_git(project_path, ["rev-parse", "--abbrev-ref", "HEAD"]).strip() or None
        status_output = self._run_git(project_path, ["status", "--porcelain"])
        dirty = bool(status_output.strip())
        return RepoState(is_git_repo=True, branch=branch, dirty=dirty)

    def diff_summary(self, project_path: Path, max_files: int = 12) -> str:
        if not self._is_git_repo(project_path):
            return "当前目录不是 Git 仓库。"

        status_output = self._run_git(project_path, ["status", "--short"]).strip()
        if not status_output:
            return "工作区干净，没有未提交变更。"

        lines = status_output.splitlines()
        selected = lines[:max_files]
        remaining = len(lines) - len(selected)

        summary = ["最近变更："]
        summary.extend(selected)
        if remaining > 0:
            summary.append(f"... 还有 {remaining} 个文件")
        return "\n".join(summary)

    def _is_git_repo(self, project_path: Path) -> bool:
        output = self._run_git(project_path, ["rev-parse", "--is-inside-work-tree"])
        return output.strip() == "true"

    def _run_git(self, project_path: Path, args: list[str]) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(project_path),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
        if proc.returncode != 0:
            return ""
        return proc.stdout
