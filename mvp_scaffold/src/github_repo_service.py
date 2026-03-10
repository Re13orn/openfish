"""Helpers for cloning public GitHub repositories into a project directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


_GITHUB_URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)
_GITHUB_SLUG_PATTERN = re.compile(r"^(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+)$")


@dataclass(slots=True)
class GitHubClonePlan:
    owner: str
    repo: str
    clone_url: str
    target_dir: Path


@dataclass(slots=True)
class GitHubCloneResult:
    ok: bool
    summary: str
    plan: GitHubClonePlan
    stdout: str = ""
    stderr: str = ""


class GitHubRepoService:
    """Validate and clone public GitHub repositories."""

    def __init__(self, *, timeout_seconds: int = 300) -> None:
        self.timeout_seconds = timeout_seconds

    def plan_clone(
        self,
        *,
        repo_input: str,
        project_root: Path,
        target_name: str | None = None,
    ) -> GitHubClonePlan:
        normalized = repo_input.strip()
        owner: str
        repo: str
        match = _GITHUB_URL_PATTERN.fullmatch(normalized)
        if match is not None:
            owner = match.group("owner")
            repo = match.group("repo")
        else:
            slug_match = _GITHUB_SLUG_PATTERN.fullmatch(normalized)
            if slug_match is None:
                raise ValueError("仅支持公开 GitHub 仓库 URL 或 owner/repo 格式。")
            owner = slug_match.group("owner")
            repo = slug_match.group("repo")

        destination_name = (target_name or repo).strip()
        if not destination_name:
            raise ValueError("目标目录不能为空。")

        destination = Path(destination_name).expanduser()
        if destination.is_absolute():
            raise ValueError("目标目录必须是相对当前项目目录的路径。")
        target_dir = (project_root / destination).resolve()

        return GitHubClonePlan(
            owner=owner,
            repo=repo,
            clone_url=f"https://github.com/{owner}/{repo}.git",
            target_dir=target_dir,
        )

    def clone(self, plan: GitHubClonePlan) -> GitHubCloneResult:
        if plan.target_dir.exists():
            raise ValueError(f"目标目录已存在: {plan.target_dir}")
        plan.target_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(  # noqa: S603
                ["git", "clone", "--depth", "1", plan.clone_url, str(plan.target_dir)],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("未找到 git 命令。") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"git clone 超时（>{self.timeout_seconds}s）。") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            detail = stderr or stdout or "unknown error"
            raise RuntimeError(detail) from exc

        return GitHubCloneResult(
            ok=True,
            summary=f"已克隆 {plan.owner}/{plan.repo} 到 {plan.target_dir}",
            plan=plan,
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
        )
