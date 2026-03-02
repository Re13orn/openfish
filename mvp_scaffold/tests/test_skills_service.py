from pathlib import Path
import subprocess

from src.skills_service import SkillsService


def _service(tmp_path: Path) -> SkillsService:
    return SkillsService(
        codex_bin="codex",
        skills_root=tmp_path / "skills",
        enable_install=True,
        timeout_seconds=30,
    )


def test_list_skills_filters_hidden_system_dirs(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    (root / "android-pentest").mkdir(parents=True)
    (root / "android-pentest" / "SKILL.md").write_text("x", encoding="utf-8")
    (root / ".system" / "skill-installer").mkdir(parents=True)
    (root / ".system" / "skill-installer" / "SKILL.md").write_text("x", encoding="utf-8")

    result = _service(tmp_path).list_skills()

    assert result.total_count == 1
    assert result.hidden_count == 1
    assert result.skills == ["android-pentest"]


def test_install_skill_rejects_unsafe_source(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    result = svc.install_skill("/tmp/local")

    assert result.ok is False
    assert "不允许本地绝对路径" in result.summary


def test_install_skill_fallback_to_legacy_subcommand(monkeypatch, tmp_path: Path) -> None:
    svc = _service(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1] == "skills":
            return subprocess.CompletedProcess(command, 2, "", "unknown command 'skills'")
        return subprocess.CompletedProcess(command, 0, "installed", "")

    monkeypatch.setattr(svc, "_run", fake_run)
    result = svc.install_skill("openai/example-skill")

    assert result.ok is True
    assert calls[0][1] == "skills"
    assert calls[1][1] == "skill"
