"""Codex CLI integration wrapper."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess

from src.config import AppConfig
from src.models import ProjectConfig


SESSION_ID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)


@dataclass(slots=True)
class CodexRunResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    summary: str
    session_id: str | None
    used_json_output: bool
    command: list[str]


class CodexRunner:
    """Executes Codex CLI commands and extracts concise results."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def run(self, project: ProjectConfig, prompt: str) -> CodexRunResult:
        """Execute a standard write-oriented Codex task."""

        command = self._build_command(project.path, prompt, use_json=self.config.codex_json_output)
        proc, used_json, resolved_command = self._execute_with_optional_fallback(command, project.path)
        return self._build_result(proc, used_json, resolved_command)

    def ask(self, project: ProjectConfig, question: str) -> CodexRunResult:
        """Execute a conservative read-oriented Codex request."""

        prompt = (
            "Read-only analysis only. Do not modify files. "
            "Answer concisely for a mobile Telegram summary.\n\n"
            f"Question: {question}"
        )
        command = self._build_command(project.path, prompt, use_json=self.config.codex_json_output)
        proc, used_json, resolved_command = self._execute_with_optional_fallback(command, project.path)
        return self._build_result(proc, used_json, resolved_command)

    def resume_last(self, project: ProjectConfig, instruction: str) -> CodexRunResult:
        """Resume the previous Codex session if supported by the CLI."""

        command = [self.config.codex_bin, "exec", "resume", "--last"]
        if self.config.codex_json_output:
            command.append("--json")
        command.append(instruction)

        return self._run_resume_with_fallback(
            project=project,
            command=command,
            instruction=instruction,
        )

    def resume_session(
        self,
        project: ProjectConfig,
        session_id: str,
        instruction: str,
    ) -> CodexRunResult:
        """Resume a specific Codex session if CLI supports explicit session target."""

        attempts: list[list[str]] = [
            [self.config.codex_bin, "exec", "resume", session_id],
            [self.config.codex_bin, "exec", "resume", "--session", session_id],
        ]
        for command in attempts:
            if self.config.codex_json_output:
                command.append("--json")
            command.append(instruction)
            result = self._run_resume_with_fallback(
                project=project,
                command=command,
                instruction=instruction,
                fallback_to_exec=False,
            )
            if result.ok:
                return result
            if not self._looks_like_resume_command_error(result.stderr):
                return result

        fallback_instruction = (
            f"Prefer resuming session {session_id} if possible.\n"
            f"{instruction}"
        )
        return self.resume_last(project, fallback_instruction)

    def _build_result(
        self,
        proc: subprocess.CompletedProcess[str],
        used_json: bool,
        resolved_command: list[str],
    ) -> CodexRunResult:

        session_id = self._extract_session_id(proc.stdout, proc.stderr)
        summary = self._build_summary(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            prefer_json_summary=used_json,
        )
        return CodexRunResult(
            ok=proc.returncode == 0,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            summary=summary,
            session_id=session_id,
            used_json_output=used_json,
            command=resolved_command,
        )

    def _build_command(self, project_path: Path, prompt: str, *, use_json: bool) -> list[str]:
        command = [self.config.codex_bin, "exec", "--cd", str(project_path)]
        if use_json:
            command.append("--json")
        if self.config.codex_default_sandbox_mode:
            command.extend(["--sandbox", self.config.codex_default_sandbox_mode])
        if self.config.codex_default_approval_mode:
            command.extend(["--ask-for-approval", self.config.codex_default_approval_mode])
        command.append(prompt)
        return command

    def _execute_with_optional_fallback(
        self, command: list[str], project_path: Path
    ) -> tuple[subprocess.CompletedProcess[str], bool, list[str]]:
        current_command = list(command)
        last_proc: subprocess.CompletedProcess[str] | None = None
        max_attempts = 8

        for _ in range(max_attempts):
            proc = self._run_subprocess(current_command, cwd=project_path)
            last_proc = proc
            if proc.returncode == 0:
                return proc, "--json" in current_command, current_command

            error_text = self._error_text(proc)
            next_command = list(current_command)

            if self._looks_like_untrusted_directory_error(error_text):
                next_command = self._insert_exec_flag(next_command, "--skip-git-repo-check")
            elif "--json" in next_command and self._looks_like_json_flag_error(error_text):
                next_command = self._remove_flag(next_command, "--json")
            elif "--skip-git-repo-check" in next_command and self._looks_like_skip_git_check_flag_error(error_text):
                next_command = self._remove_flag(next_command, "--skip-git-repo-check")
            elif self._looks_like_approval_flag_error(error_text):
                next_command = self._remove_flag_with_value(next_command, "--ask-for-approval")
            elif self._looks_like_sandbox_flag_error(error_text):
                next_command = self._remove_flag_with_value(next_command, "--sandbox")

            if next_command == current_command:
                break
            current_command = next_command

        if last_proc is None:
            last_proc = self._run_subprocess(current_command, cwd=project_path)
        return last_proc, "--json" in current_command, current_command

    def _run_subprocess(self, command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self.config.codex_command_timeout_seconds,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                args=command,
                returncode=127,
                stdout="",
                stderr=f"Codex binary not found: {self.config.codex_bin}",
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                args=command,
                returncode=124,
                stdout="",
                stderr=f"Codex command timed out after {self.config.codex_command_timeout_seconds}s",
            )

    def _looks_like_json_flag_error(self, stderr: str) -> bool:
        lower = stderr.lower()
        return "--json" in lower and ("unknown option" in lower or "unexpected argument" in lower)

    def _looks_like_approval_flag_error(self, stderr: str) -> bool:
        lower = stderr.lower()
        return "--ask-for-approval" in lower and (
            "unknown option" in lower or "unexpected argument" in lower or "unrecognized option" in lower
        )

    def _looks_like_sandbox_flag_error(self, stderr: str) -> bool:
        lower = stderr.lower()
        return "--sandbox" in lower and (
            "unknown option" in lower or "unexpected argument" in lower or "unrecognized option" in lower
        )

    def _looks_like_skip_git_check_flag_error(self, stderr: str) -> bool:
        lower = stderr.lower()
        return "--skip-git-repo-check" in lower and (
            "unknown option" in lower or "unexpected argument" in lower or "unrecognized option" in lower
        )

    def _looks_like_untrusted_directory_error(self, stderr: str) -> bool:
        lower = stderr.lower()
        return "not inside a trusted directory" in lower and "--skip-git-repo-check" in lower

    def _looks_like_resume_command_error(self, stderr: str) -> bool:
        lower = stderr.lower()
        return "resume" in lower and (
            "unknown" in lower or "unexpected argument" in lower or "invalid value" in lower
        )

    def _run_resume_with_fallback(
        self,
        *,
        project: ProjectConfig,
        command: list[str],
        instruction: str,
        fallback_to_exec: bool = True,
    ) -> CodexRunResult:
        proc = self._run_subprocess(command, cwd=project.path)
        used_json = self.config.codex_json_output
        if proc.returncode != 0 and fallback_to_exec and self._looks_like_resume_command_error(proc.stderr):
            fallback_prompt = (
                "Continue the previous task context if available and report remaining blockers.\n\n"
                f"Instruction: {instruction}"
            )
            fallback_command = self._build_command(
                project.path, fallback_prompt, use_json=self.config.codex_json_output
            )
            fallback_proc, used_json, resolved_command = self._execute_with_optional_fallback(
                fallback_command, project.path
            )
            return self._build_result(fallback_proc, used_json, resolved_command)

        return self._build_result(proc, used_json, command)

    def _extract_session_id(self, stdout: str, stderr: str) -> str | None:
        for text in (stdout, stderr):
            if not text:
                continue
            match = SESSION_ID_PATTERN.search(text)
            if match:
                return match.group(0)
        return None

    def _build_summary(self, *, exit_code: int, stdout: str, stderr: str, prefer_json_summary: bool) -> str:
        if prefer_json_summary:
            json_summary = self._extract_json_summary(stdout)
            if json_summary:
                return json_summary

        if exit_code == 0:
            return self._truncate(stdout.strip() or "Codex completed successfully.", 500)

        error_source = stderr.strip() or stdout.strip() or f"Codex failed with exit code {exit_code}."
        return self._truncate(error_source, 500)

    def _extract_json_summary(self, stdout: str) -> str | None:
        last_text: str | None = None
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped or not stripped.startswith("{"):
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            candidate = self._extract_text_from_json(payload)
            if candidate:
                last_text = candidate
        return self._truncate(last_text, 500) if last_text else None

    def _extract_text_from_json(self, payload: dict) -> str | None:
        for key in ("summary", "output_text", "text", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        event = payload.get("event")
        if isinstance(event, dict):
            for key in ("summary", "text", "message"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _remove_flag(self, command: list[str], flag: str) -> list[str]:
        return [item for item in command if item != flag]

    def _remove_flag_with_value(self, command: list[str], flag: str) -> list[str]:
        reduced: list[str] = []
        skip_next = False
        for index, item in enumerate(command):
            if skip_next:
                skip_next = False
                continue
            if item == flag:
                if index + 1 < len(command):
                    skip_next = True
                continue
            reduced.append(item)
        return reduced

    def _insert_exec_flag(self, command: list[str], flag: str) -> list[str]:
        if flag in command:
            return list(command)
        try:
            exec_index = command.index("exec")
        except ValueError:
            return list(command) + [flag]
        insert_index = exec_index + 1
        return command[:insert_index] + [flag] + command[insert_index:]

    def _error_text(self, proc: subprocess.CompletedProcess[str]) -> str:
        return f"{proc.stderr}\n{proc.stdout}"
