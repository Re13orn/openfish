"""Approval policy helpers for Phase 3 workflow."""

from dataclasses import dataclass
import re

from src.codex_runner import CodexRunResult


APPROVAL_PATTERNS = [
    re.compile(r"\bneeds?\s+approval\b", re.IGNORECASE),
    re.compile(r"\bwaiting\s+for\s+approval\b", re.IGNORECASE),
    re.compile(r"\brequires?\s+approval\b", re.IGNORECASE),
    re.compile(r"\bapprove\b", re.IGNORECASE),
    re.compile(r"等待审批"),
    re.compile(r"需要审批"),
    re.compile(r"请批准"),
]


@dataclass(slots=True)
class ApprovalAssessment:
    requires_approval: bool
    reason: str | None


class ApprovalService:
    """Detects approval pauses from Codex output and builds follow-up instructions."""

    def assess(self, result: CodexRunResult) -> ApprovalAssessment:
        text_parts = [result.summary or "", result.stderr or "", result.stdout or ""]
        combined = "\n".join(part for part in text_parts if part).strip()
        if not combined:
            return ApprovalAssessment(requires_approval=False, reason=None)

        for pattern in APPROVAL_PATTERNS:
            if pattern.search(combined):
                reason = self._extract_reason(combined)
                return ApprovalAssessment(requires_approval=True, reason=reason)
        return ApprovalAssessment(requires_approval=False, reason=None)

    def build_resume_instruction(self, requested_action: str, user_note: str | None = None) -> str:
        base = (
            "Approval granted. Continue the previously blocked task with conservative changes and concise summary. "
            f"Approved action context: {requested_action}"
        )
        if user_note:
            return f"{base}\nUser note: {user_note}"
        return base

    def _extract_reason(self, text: str, max_len: int = 240) -> str:
        for line in text.splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            if any(p.search(candidate) for p in APPROVAL_PATTERNS):
                return candidate[:max_len]
        return text[:max_len]
