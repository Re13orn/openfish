"""Local Codex skills discovery and safe install helpers."""

from dataclasses import dataclass
from pathlib import Path
import subprocess
from urllib.parse import urlparse


@dataclass(slots=True)
class SkillsListResult:
    skills_root: Path
    skills: list[str]
    total_count: int
    hidden_count: int
    omitted_count: int


@dataclass(slots=True)
class SkillInstallResult:
    ok: bool
    source: str
    summary: str
    stdout: str
    stderr: str
    command: list[str] | None


class SkillsService:
    """Manage Codex skills under local CODEX_HOME."""

    def __init__(
        self,
        *,
        codex_bin: str,
        skills_root: Path,
        enable_install: bool,
        timeout_seconds: int,
    ) -> None:
        self.codex_bin = codex_bin
        self.skills_root = skills_root
        self.enable_install = enable_install
        self.timeout_seconds = timeout_seconds

    def list_skills(self, *, limit: int = 30) -> SkillsListResult:
        if not self.skills_root.exists():
            return SkillsListResult(
                skills_root=self.skills_root,
                skills=[],
                total_count=0,
                hidden_count=0,
                omitted_count=0,
            )

        visible: list[str] = []
        hidden_count = 0

        for skill_md in sorted(self.skills_root.rglob("SKILL.md")):
            relative = skill_md.parent.relative_to(self.skills_root)
            identifier = relative.as_posix()
            if self._is_hidden_skill(relative):
                hidden_count += 1
                continue
            visible.append(identifier)

        total_count = len(visible)
        shown = visible[:limit]
        omitted_count = max(0, total_count - len(shown))
        return SkillsListResult(
            skills_root=self.skills_root,
            skills=shown,
            total_count=total_count,
            hidden_count=hidden_count,
            omitted_count=omitted_count,
        )

    def install_skill(self, source: str) -> SkillInstallResult:
        if not self.enable_install:
            return SkillInstallResult(
                ok=False,
                source=source,
                summary="当前已禁用 skill 安装。请设置 ENABLE_SKILL_INSTALL=true。",
                stdout="",
                stderr="",
                command=None,
            )

        normalized_source, err = self._normalize_source(source)
        if err:
            return SkillInstallResult(
                ok=False,
                source=source,
                summary=err,
                stdout="",
                stderr="",
                command=None,
            )

        attempts = [
            [self.codex_bin, "skills", "install", normalized_source],
            [self.codex_bin, "skill", "install", normalized_source],
        ]

        last_proc: subprocess.CompletedProcess[str] | None = None
        last_command: list[str] | None = None

        for command in attempts:
            proc = self._run(command)
            last_proc = proc
            last_command = command
            if proc.returncode == 0:
                summary = self._shorten(proc.stdout.strip() or "Skill 安装成功。")
                return SkillInstallResult(
                    ok=True,
                    source=normalized_source,
                    summary=summary,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    command=command,
                )
            if not self._looks_like_unknown_subcommand(proc.stdout, proc.stderr):
                error_text = proc.stderr.strip() or proc.stdout.strip() or "Skill 安装失败。"
                return SkillInstallResult(
                    ok=False,
                    source=normalized_source,
                    summary=self._shorten(error_text),
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    command=command,
                )

        unsupported = (
            "当前 Codex CLI 似乎不支持 skills 安装子命令。"
            "请先在本机确认 codex 版本，或手动安装 skill。"
        )
        return SkillInstallResult(
            ok=False,
            source=normalized_source,
            summary=unsupported,
            stdout=last_proc.stdout if last_proc else "",
            stderr=last_proc.stderr if last_proc else "",
            command=last_command,
        )

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                args=command,
                returncode=127,
                stdout="",
                stderr=f"Codex binary not found: {self.codex_bin}",
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                args=command,
                returncode=124,
                stdout="",
                stderr=f"Skill install timed out after {self.timeout_seconds}s",
            )

    def _normalize_source(self, source: str) -> tuple[str, str | None]:
        value = source.strip()
        if not value:
            return "", "用法: /skill-install <source>"
        if len(value) > 200:
            return "", "source 过长，请缩短到 200 字符以内。"
        if any(ch in value for ch in ("\n", "\r", "\t")):
            return "", "source 包含非法控制字符。"
        if value.startswith("-"):
            return "", "source 不能以 '-' 开头。"
        if value.startswith("/") or value.startswith("~"):
            return "", "仅允许远程 source，不允许本地绝对路径。"
        if ".." in value:
            return "", "source 不能包含 '..'。"

        if "://" in value:
            parsed = urlparse(value)
            if parsed.scheme not in {"https"}:
                return "", "仅允许 https:// 链接。"
            if parsed.netloc.lower() != "github.com":
                return "", "仅允许 github.com 来源。"
        return value, None

    def _looks_like_unknown_subcommand(self, stdout: str, stderr: str) -> bool:
        text = f"{stdout}\n{stderr}".lower()
        return (
            "unknown command" in text
            or "unknown option" in text
            or "unrecognized option" in text
            or "unexpected argument" in text
            or "invalid value" in text
        ) and "skill" in text

    def _is_hidden_skill(self, relative: Path) -> bool:
        return any(part.startswith(".") for part in relative.parts)

    def _shorten(self, text: str, limit: int = 220) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."
