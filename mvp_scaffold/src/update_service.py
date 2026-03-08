"""Git-based self-update helpers for OpenFish."""

from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess


@dataclass(slots=True)
class VersionInfo:
    branch: str
    version: str
    commit: str


@dataclass(slots=True)
class UpdateCheckResult:
    ok: bool
    current: VersionInfo | None
    upstream_ref: str | None
    upstream_commit: str | None
    behind_count: int
    ahead_count: int
    commits: list[str]
    summary: str


@dataclass(slots=True)
class UpdateTriggerResult:
    ok: bool
    summary: str
    script_path: Path


@dataclass(slots=True)
class LogsResult:
    ok: bool
    text: str
    app_log_path: Path
    update_log_path: Path


class UpdateService:
    """Provides repository version checks and local service control helpers."""

    def __init__(self, *, repo_root: Path, script_path: Path, log_dir: Path | None = None) -> None:
        self.repo_root = repo_root
        self.script_path = script_path
        self.log_dir = log_dir or (repo_root / "mvp_scaffold/data/logs")
        self.app_log_path = self.log_dir / "app.out.log"
        self.update_log_path = self.log_dir / "update.log"

    def get_current_version(self) -> VersionInfo:
        return VersionInfo(
            branch=self._git("rev-parse", "--abbrev-ref", "HEAD"),
            version=self._git("describe", "--tags", "--always", "--dirty"),
            commit=self._git("rev-parse", "--short", "HEAD"),
        )

    def check_for_updates(self) -> UpdateCheckResult:
        current = self.get_current_version()
        upstream_ref = self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        remote_name, remote_branch = upstream_ref.split("/", 1)
        self._run(["git", "-C", str(self.repo_root), "fetch", "--quiet", remote_name, remote_branch])
        upstream_commit = self._git("rev-parse", "--short", upstream_ref)
        behind_count = int(self._git("rev-list", "--count", f"HEAD..{upstream_ref}"))
        ahead_count = int(self._git("rev-list", "--count", f"{upstream_ref}..HEAD"))
        commits = []
        if behind_count > 0:
            log_output = self._git("log", "--oneline", "--no-decorate", f"HEAD..{upstream_ref}")
            commits = [line.strip() for line in log_output.splitlines() if line.strip()][:5]
            summary = f"发现 {behind_count} 个上游更新。"
        else:
            summary = "当前已是最新版本。"
        return UpdateCheckResult(
            ok=True,
            current=current,
            upstream_ref=upstream_ref,
            upstream_commit=upstream_commit,
            behind_count=behind_count,
            ahead_count=ahead_count,
            commits=commits,
            summary=summary,
        )

    def trigger_update(self) -> UpdateTriggerResult:
        self._trigger_script_after_delay("update")
        return UpdateTriggerResult(
            ok=True,
            summary=f"已开始自更新。过程日志: {self.update_log_path}",
            script_path=self.script_path,
        )

    def trigger_restart(self) -> UpdateTriggerResult:
        self._trigger_script_after_delay("restart")
        return UpdateTriggerResult(
            ok=True,
            summary=f"已开始重启。过程日志: {self.update_log_path}",
            script_path=self.script_path,
        )

    def read_logs(self, *, app_lines: int = 40, update_lines: int = 20) -> LogsResult:
        app_text = self._tail_file(self.app_log_path, app_lines)
        update_text = self._tail_file(self.update_log_path, update_lines)
        sections: list[str] = []
        sections.append(f"运行日志 ({self.app_log_path.name}):")
        sections.append(app_text or "暂无日志。")
        if update_text:
            sections.append("")
            sections.append(f"更新日志 ({self.update_log_path.name}):")
            sections.append(update_text)
        return LogsResult(
            ok=True,
            text="\n".join(sections),
            app_log_path=self.app_log_path,
            update_log_path=self.update_log_path,
        )

    def clear_logs(self) -> LogsResult:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.app_log_path.write_text("", encoding="utf-8")
        self.update_log_path.write_text("", encoding="utf-8")
        return LogsResult(
            ok=True,
            text=(
                "已清空日志：\n"
                f"- {self.app_log_path}\n"
                f"- {self.update_log_path}"
            ),
            app_log_path=self.app_log_path,
            update_log_path=self.update_log_path,
        )

    def _git(self, *args: str) -> str:
        return self._run(["git", "-C", str(self.repo_root), *args]).stdout.strip()

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"命令不存在: {command[0]}") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            detail = stderr or stdout or "unknown error"
            raise RuntimeError(detail) from exc

    def _tail_file(self, path: Path, lines: int) -> str:
        if not path.exists():
            return ""
        content = path.read_text(encoding="utf-8", errors="replace")
        excerpt = content.splitlines()[-max(1, lines):]
        return "\n".join(excerpt).strip()

    def _trigger_script_after_delay(self, command: str) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        shell_command = (
            f"sleep 2; exec bash {shlex.quote(str(self.script_path))} {shlex.quote(command)}"
        )
        with self.update_log_path.open("ab") as handle:
            subprocess.Popen(  # noqa: S603
                ["bash", "-lc", shell_command],
                cwd=str(self.repo_root),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
