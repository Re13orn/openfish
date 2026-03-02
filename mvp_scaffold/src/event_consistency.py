"""Static checks for audit/task event code consistency."""

from __future__ import annotations

import ast
from pathlib import Path

from src import audit_events


def scan_event_consistency(paths: list[Path]) -> list[str]:
    """Return a list of violations found in Python source files."""

    violations: list[str] = []
    for path in paths:
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            violations.append(f"{path}: syntax error: {exc.msg}")
            continue
        checker = _EventChecker(path=path)
        checker.visit(tree)
        violations.extend(checker.violations)
    return violations


class _EventChecker(ast.NodeVisitor):
    def __init__(self, *, path: Path) -> None:
        self.path = path
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        self._check_audit_log_call(node)
        self._check_task_event_call(node)
        self.generic_visit(node)

    def _check_audit_log_call(self, node: ast.Call) -> None:
        if not _is_attribute_call(node, attr_name="log"):
            return
        action_expr = None
        for keyword in node.keywords:
            if keyword.arg == "action":
                action_expr = keyword.value
                break
        if action_expr is None:
            return

        if not _is_valid_audit_action_expr(action_expr):
            self._add_violation(node, "invalid audit action expression")

    def _check_task_event_call(self, node: ast.Call) -> None:
        if not _is_attribute_call(node, attr_name="_insert_task_event"):
            return
        if len(node.args) < 3:
            return
        event_expr = node.args[2]
        if not _is_valid_task_event_expr(event_expr):
            self._add_violation(node, "invalid task event expression")

    def _add_violation(self, node: ast.AST, reason: str) -> None:
        line = getattr(node, "lineno", 1)
        self.violations.append(f"{self.path}:{line}: {reason}")


def _is_attribute_call(node: ast.Call, *, attr_name: str) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr == attr_name


def _is_valid_audit_action_expr(expr: ast.AST) -> bool:
    if isinstance(expr, ast.IfExp):
        return _is_valid_audit_action_expr(expr.body) and _is_valid_audit_action_expr(expr.orelse)
    if isinstance(expr, ast.Attribute):
        return isinstance(expr.value, ast.Name) and expr.value.id == "audit_events"
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value in audit_events.ALL_EVENTS
    return False


def _is_valid_task_event_expr(expr: ast.AST) -> bool:
    if isinstance(expr, ast.IfExp):
        return _is_valid_task_event_expr(expr.body) and _is_valid_task_event_expr(expr.orelse)
    if isinstance(expr, ast.Name):
        return True
    if isinstance(expr, ast.Attribute):
        return isinstance(expr.value, ast.Name) and expr.value.id == "audit_events"
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value in audit_events.TASK_EVENT_TYPES or expr.value.startswith("task.status.")
    return False
