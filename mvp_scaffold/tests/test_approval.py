from src.approval import ApprovalService
from src.codex_runner import CodexRunResult


def _result(summary: str, stdout: str = "", stderr: str = "") -> CodexRunResult:
    return CodexRunResult(
        ok=True,
        stdout=stdout,
        stderr=stderr,
        exit_code=0,
        summary=summary,
        session_id=None,
        used_json_output=False,
        command=["codex", "exec"],
    )


def test_approval_detects_english_hint() -> None:
    service = ApprovalService()
    assessment = service.assess(_result("Waiting for approval before continue"))
    assert assessment.requires_approval is True


def test_approval_detects_chinese_hint() -> None:
    service = ApprovalService()
    assessment = service.assess(_result("此步骤需要审批后继续"))
    assert assessment.requires_approval is True


def test_approval_not_required_for_normal_summary() -> None:
    service = ApprovalService()
    assessment = service.assess(_result("任务执行完成，测试通过"))
    assert assessment.requires_approval is False
