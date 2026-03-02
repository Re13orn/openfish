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
        )
        super().__init__(config)
        self._responses = responses
        self.commands: list[list[str]] = []

    def _run_subprocess(self, command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        _ = cwd
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
