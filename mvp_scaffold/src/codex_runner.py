"""Codex CLI integration wrapper."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from threading import Thread
from typing import Callable

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
    display_text: str | None = None


class CodexRunner:
    """Executes Codex CLI commands and extracts concise results."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def run(
        self,
        project: ProjectConfig,
        prompt: str,
        *,
        model: str | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> CodexRunResult:
        """Execute a standard write-oriented Codex task."""

        command = self._build_command(
            project.path,
            prompt,
            use_json=self.config.codex_json_output,
            model=model,
        )
        proc, used_json, resolved_command = self._execute_with_optional_fallback(
            command,
            project.path,
            progress_callback=progress_callback,
        )
        return self._build_result(proc, used_json, resolved_command)

    def ask(
        self,
        project: ProjectConfig,
        question: str,
        *,
        model: str | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> CodexRunResult:
        """Execute a conservative read-oriented Codex request."""

        prompt = self._build_ask_prompt(question)
        command = self._build_command(
            project.path,
            prompt,
            use_json=self.config.codex_json_output,
            model=model,
        )
        proc, used_json, resolved_command = self._execute_with_optional_fallback(
            command,
            project.path,
            progress_callback=progress_callback,
        )
        return self._build_result(proc, used_json, resolved_command)

    def ask_in_session(
        self,
        project: ProjectConfig,
        session_id: str,
        question: str,
        *,
        model: str | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> CodexRunResult:
        """Continue an existing session with a read-only analysis request."""

        return self.resume_session(
            project,
            session_id,
            self._build_ask_prompt(question),
            model=model,
            progress_callback=progress_callback,
        )

    def resume_last(
        self,
        project: ProjectConfig,
        instruction: str,
        *,
        model: str | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> CodexRunResult:
        """Resume the previous Codex session if supported by the CLI."""

        command = [self.config.codex_bin, "exec", "resume", "--last"]
        if model:
            command.extend(["-m", model])
        if self.config.codex_json_output:
            command.append("--json")
        command.append(instruction)

        return self._run_resume_with_fallback(
            project=project,
            command=command,
            instruction=instruction,
            model=model,
            progress_callback=progress_callback,
        )

    def resume_session(
        self,
        project: ProjectConfig,
        session_id: str,
        instruction: str,
        *,
        model: str | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> CodexRunResult:
        """Resume a specific Codex session if CLI supports explicit session target."""

        attempts: list[list[str]] = [
            [self.config.codex_bin, "exec", "resume", session_id],
            [self.config.codex_bin, "exec", "resume", "--session", session_id],
        ]
        for command in attempts:
            if model:
                command.extend(["-m", model])
            if self.config.codex_json_output:
                command.append("--json")
            command.append(instruction)
            result = self._run_resume_with_fallback(
                project=project,
                command=command,
                instruction=instruction,
                fallback_to_exec=False,
                model=model,
                progress_callback=progress_callback,
            )
            if result.ok:
                return result
            if not self._looks_like_resume_command_error(result.stderr):
                return result

        fallback_instruction = (
            f"Prefer resuming session {session_id} if possible.\n"
            f"{instruction}"
        )
        return self.resume_last(
            project,
            fallback_instruction,
            model=model,
            progress_callback=progress_callback,
        )

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
        display_text = self._build_display_text(
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
            display_text=display_text,
        )

    def _build_command(
        self,
        project_path: Path,
        prompt: str,
        *,
        use_json: bool,
        model: str | None = None,
    ) -> list[str]:
        command = [self.config.codex_bin, "exec", "--cd", str(project_path)]
        if model:
            command.extend(["-m", model])
        if use_json:
            command.append("--json")
        if self.config.codex_default_sandbox_mode:
            command.extend(["--sandbox", self.config.codex_default_sandbox_mode])
        approval_mode = self._effective_approval_mode()
        if approval_mode:
            command.extend(["--ask-for-approval", approval_mode])
        command.append(prompt)
        return command

    def _build_ask_prompt(self, question: str) -> str:
        return (
            "Read-only analysis only. Do not modify files. "
            "Answer concisely for a mobile Telegram summary.\n\n"
            f"Question: {question}"
        )

    def _execute_with_optional_fallback(
        self,
        command: list[str],
        project_path: Path,
        *,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], bool, list[str]]:
        current_command = list(command)
        last_proc: subprocess.CompletedProcess[str] | None = None
        max_attempts = 8

        for _ in range(max_attempts):
            proc = self._run_subprocess(current_command, cwd=project_path, progress_callback=progress_callback)
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
            last_proc = self._run_subprocess(current_command, cwd=project_path, progress_callback=progress_callback)
        return last_proc, "--json" in current_command, current_command

    def _run_subprocess(
        self,
        command: list[str],
        *,
        cwd: Path,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if progress_callback is None:
            return self._run_subprocess_blocking(command, cwd=cwd)
        return self._run_subprocess_streaming(command, cwd=cwd, progress_callback=progress_callback)

    def _run_subprocess_blocking(self, command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
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

    def _run_subprocess_streaming(
        self,
        command: list[str],
        *,
        cwd: Path,
        progress_callback: Callable[[str, str], None],
    ) -> subprocess.CompletedProcess[str]:
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                args=command,
                returncode=127,
                stdout="",
                stderr=f"Codex binary not found: {self.config.codex_bin}",
            )

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        def _reader(stream, sink: list[str], channel: str) -> None:  # noqa: ANN001
            try:
                for line in iter(stream.readline, ""):
                    sink.append(line)
                    normalized = self._normalize_progress_line(channel, line)
                    if normalized:
                        progress_callback(channel, normalized)
            finally:
                stream.close()

        stdout_thread = Thread(target=_reader, args=(proc.stdout, stdout_parts, "stdout"), daemon=True)
        stderr_thread = Thread(target=_reader, args=(proc.stderr, stderr_parts, "stderr"), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        try:
            returncode = proc.wait(timeout=self.config.codex_command_timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            stdout_thread.join(timeout=1.0)
            stderr_thread.join(timeout=1.0)
            stderr_text = "".join(stderr_parts)
            timeout_message = f"Codex command timed out after {self.config.codex_command_timeout_seconds}s"
            if stderr_text:
                stderr_text = f"{stderr_text.rstrip()}\n{timeout_message}"
            else:
                stderr_text = timeout_message
            return subprocess.CompletedProcess(
                args=command,
                returncode=124,
                stdout="".join(stdout_parts),
                stderr=stderr_text,
            )

        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
        return subprocess.CompletedProcess(
            args=command,
            returncode=returncode,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
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
        model: str | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> CodexRunResult:
        proc = self._run_subprocess(command, cwd=project.path, progress_callback=progress_callback)
        used_json = self.config.codex_json_output
        if proc.returncode != 0 and fallback_to_exec and self._looks_like_resume_command_error(proc.stderr):
            fallback_prompt = (
                "Continue the previous task context if available and report remaining blockers.\n\n"
                f"Instruction: {instruction}"
            )
            fallback_command = self._build_command(
                project.path,
                fallback_prompt,
                use_json=self.config.codex_json_output,
                model=model,
            )
            fallback_proc, used_json, resolved_command = self._execute_with_optional_fallback(
                fallback_command,
                project.path,
                progress_callback=progress_callback,
            )
            return self._build_result(fallback_proc, used_json, resolved_command)

        return self._build_result(proc, used_json, command)

    def _effective_approval_mode(self) -> str | None:
        mode = (self.config.codex_default_approval_mode or "").strip()
        if not mode:
            return None
        # OpenFish runs Codex as a non-interactive subprocess. "on-request" can block forever
        # waiting for CLI-side approval, so rely on OpenFish's own approval workflow instead.
        if mode == "on-request":
            return "never"
        return mode

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

    def _build_display_text(self, *, exit_code: int, stdout: str, stderr: str, prefer_json_summary: bool) -> str | None:
        if prefer_json_summary:
            json_text = self._extract_json_display_text(stdout)
            if json_text:
                return self._truncate(json_text, 12000)
        if exit_code == 0:
            content = stdout.strip()
            return self._truncate(content, 12000) if content else None
        error_source = stderr.strip() or stdout.strip()
        if not error_source:
            return None
        return self._truncate(error_source, 12000)

    def _extract_json_display_text(self, stdout: str) -> str | None:
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
        return last_text

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

    def _normalize_progress_line(self, channel: str, line: str) -> str | None:
        stripped = line.strip()
        if not stripped:
            return None
        if self._is_ignorable_progress_line(channel, stripped):
            return None
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                candidate = self._extract_text_from_json(payload)
                if candidate:
                    return candidate
                event = payload.get("event")
                msg = payload.get("msg") or payload.get("message")
                if isinstance(event, str) and isinstance(msg, str) and msg.strip():
                    return f"{event}: {msg.strip()}"
        if channel == "stderr":
            return f"[stderr] {stripped}"
        return stripped

    def _is_ignorable_progress_line(self, channel: str, line: str) -> bool:
        lower = line.lower()
        if channel != "stderr":
            return False
        if lower.startswith("mcp: ") and (" ready" in lower or "failed" in lower):
            return True
        if lower.startswith("mcp::transport:") or lower.startswith("mcp::transport::"):
            return True
        if "mcp client for `" in lower and "failed to start" in lower:
            return True
        if "transport channel closed" in lower and "mcp" in lower:
            return True
        if "unexpected content type:" in lower and "mcp" in lower:
            return True
        return False

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
