from pathlib import Path
import subprocess
from types import SimpleNamespace

from src.codex_runner import CodexRunner
from src.models import ProjectConfig


class StubCodexRunner(CodexRunner):
    def __init__(self, responses: list[subprocess.CompletedProcess[str]]) -> None:
        config = SimpleNamespace(
            codex_bin="codex",
            codex_json_output=True,
            codex_default_sandbox_mode="workspace-write",
            codex_default_approval_mode="on-request",
            codex_command_timeout_seconds=30,
            codex_background_terminal_wait_timeout_seconds=120,
            codex_model_choices=("gpt-5.4", "gpt-5", "o3"),
        )
        super().__init__(config)
        self._responses = responses
        self.commands: list[list[str]] = []

    def _run_subprocess(
        self,
        command: list[str],
        *,
        cwd: Path,
        progress_callback=None,
        process_callback=None,
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd
        _ = progress_callback
        _ = process_callback
        self.commands.append(list(command))
        if not self._responses:
            raise RuntimeError("No stub response left.")
        return self._responses.pop(0)


def _project() -> ProjectConfig:
    return ProjectConfig(
        key="demo",
        name="Demo",
        path=Path("/tmp"),
        allowed_directories=[Path("/tmp")],
    )


def test_fallback_removes_unsupported_approval_flag() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="error: unexpected argument '--ask-for-approval' found",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.run(_project(), "hello")

    assert result.ok is True
    assert len(runner.commands) == 2
    assert "--ask-for-approval" in runner.commands[0]
    assert "--ask-for-approval" not in runner.commands[1]
    assert result.command == runner.commands[1]


def test_on_request_approval_mode_is_coerced_to_never_for_noninteractive_runs() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.run(_project(), "hello")

    assert result.ok is True
    assert "--ask-for-approval" in runner.commands[0]
    approval_flag_index = runner.commands[0].index("--ask-for-approval")
    assert runner.commands[0][approval_flag_index + 1] == "never"


def test_model_is_passed_to_codex_exec() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.run(_project(), "hello", model="o3")

    assert result.ok is True
    assert "-m" in runner.commands[0]
    model_index = runner.commands[0].index("-m")
    assert runner.commands[0][model_index + 1] == "o3"


def test_fallback_removes_json_then_approval_flag() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="error: unexpected argument '--json' found",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="error: unexpected argument '--ask-for-approval' found",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.run(_project(), "hello")

    assert result.ok is True
    assert len(runner.commands) == 3
    assert "--json" in runner.commands[0]
    assert "--json" not in runner.commands[1]
    assert "--ask-for-approval" not in runner.commands[2]
    assert result.used_json_output is False


def test_fallback_removes_unsupported_sandbox_flag() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="error: unexpected argument '--sandbox' found",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.run(_project(), "hello")

    assert result.ok is True
    assert len(runner.commands) == 2
    assert "--sandbox" in runner.commands[0]
    assert "--sandbox" not in runner.commands[1]


def test_fallback_adds_skip_git_repo_check_for_untrusted_directory() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="Not inside a trusted directory and --skip-git-repo-check was not specified.",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.run(_project(), "hello")

    assert result.ok is True
    assert len(runner.commands) == 2
    assert "--skip-git-repo-check" not in runner.commands[0]
    assert "--skip-git-repo-check" in runner.commands[1]


def test_fallback_removes_skip_git_repo_check_when_not_supported() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="Not inside a trusted directory and --skip-git-repo-check was not specified.",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="error: unexpected argument '--skip-git-repo-check' found",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.run(_project(), "hello")

    assert result.ok is True
    assert len(runner.commands) == 3
    assert "--skip-git-repo-check" in runner.commands[1]
    assert "--skip-git-repo-check" not in runner.commands[2]


def test_fallback_adds_skip_git_repo_check_when_error_in_stdout() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="Not inside a trusted directory and --skip-git-repo-check was not specified.",
                stderr="",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.run(_project(), "hello")

    assert result.ok is True
    assert len(runner.commands) == 2
    assert "--skip-git-repo-check" in runner.commands[1]


def test_fallback_handles_flag_error_then_untrusted_directory() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="error: unexpected argument '--ask-for-approval' found",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="Not inside a trusted directory and --skip-git-repo-check was not specified.",
                stderr="",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.run(_project(), "hello")

    assert result.ok is True
    assert len(runner.commands) == 3
    assert "--ask-for-approval" in runner.commands[0]
    assert "--ask-for-approval" not in runner.commands[1]
    assert "--skip-git-repo-check" in runner.commands[2]


def test_resume_session_uses_explicit_session_command() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.resume_session(_project(), "sess-123", "continue")

    assert result.ok is True
    assert len(runner.commands) == 1
    assert runner.commands[0][:4] == ["codex", "exec", "resume", "sess-123"]


def test_resume_session_adds_skip_git_repo_check_for_untrusted_directory() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="Not inside a trusted directory and --skip-git-repo-check was not specified.",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.resume_session(_project(), "sess-123", "continue")

    assert result.ok is True
    assert len(runner.commands) == 2
    assert "--skip-git-repo-check" not in runner.commands[0]
    assert "--skip-git-repo-check" in runner.commands[1]


def test_resume_session_removes_skip_git_repo_check_when_not_supported() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="Not inside a trusted directory and --skip-git-repo-check was not specified.",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="error: unexpected argument '--skip-git-repo-check' found",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    result = runner.resume_session(_project(), "sess-123", "continue")

    assert result.ok is True
    assert len(runner.commands) == 3
    assert "--skip-git-repo-check" in runner.commands[1]
    assert "--skip-git-repo-check" not in runner.commands[2]


def test_normalize_progress_line_extracts_json_text() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    normalized = runner._normalize_progress_line("stdout", '{"text":"Working on file a.py"}')

    assert normalized == "Working on file a.py"


def test_normalize_progress_line_ignores_mcp_startup_noise() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    assert runner._normalize_progress_line("stderr", "mcp: playwright ready") is None
    assert (
        runner._normalize_progress_line(
            "stderr",
            "mcp: android-control failed: MCP client for `android-control` failed to start: foo",
        )
        is None
    )
    assert (
        runner._normalize_progress_line(
            "stderr",
            "2026-03-07T06:00:56Z ERROR mcp::transport::worker: worker quit with fatal: Transport channel closed",
        )
        is None
    )


def test_normalize_progress_line_keeps_non_mcp_stderr() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    normalized = runner._normalize_progress_line("stderr", "pytest failed: 2 tests failed")

    assert normalized == "[stderr] pytest failed: 2 tests failed"


def test_detects_background_terminal_wait_line() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    assert runner._looks_like_background_terminal_wait("Waited for background terminal") is True
    assert runner._looks_like_background_terminal_wait("other output") is False


def test_stream_timeout_message_uses_background_terminal_timeout() -> None:
    runner = StubCodexRunner(
        responses=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    message = runner._stream_timeout_message(
        now=121.0,
        deadline=500.0,
        background_wait_started_at=0.0,
    )

    assert message == "Codex command aborted after waiting too long for a background terminal (120s)."
