from pathlib import Path

import pytest

from src.github_repo_service import GitHubRepoService


def test_plan_clone_accepts_github_url_and_relative_dir() -> None:
    service = GitHubRepoService()

    plan = service.plan_clone(
        repo_input="https://github.com/openai/openai-python",
        project_root=Path("/tmp/project"),
        target_name="vendor/openai-python",
    )

    assert plan.owner == "openai"
    assert plan.repo == "openai-python"
    assert plan.clone_url == "https://github.com/openai/openai-python.git"
    assert plan.target_dir == Path("/tmp/project/vendor/openai-python").resolve()


def test_plan_clone_accepts_owner_repo_slug() -> None:
    service = GitHubRepoService()

    plan = service.plan_clone(
        repo_input="octocat/Hello-World",
        project_root=Path("/tmp/project"),
    )

    assert plan.owner == "octocat"
    assert plan.repo == "Hello-World"
    assert plan.target_dir == Path("/tmp/project/Hello-World").resolve()


def test_plan_clone_rejects_non_github_inputs() -> None:
    service = GitHubRepoService()

    with pytest.raises(ValueError, match="仅支持公开 GitHub 仓库 URL 或 owner/repo 格式"):
        service.plan_clone(
            repo_input="https://gitlab.com/group/repo",
            project_root=Path("/tmp/project"),
        )
