"""Safe wrappers for reading Codex MCP configuration."""

from dataclasses import dataclass
import json
import re
import subprocess
from typing import Any

from src.redaction import redact_text


@dataclass(slots=True)
class McpServerSummary:
    name: str
    enabled: bool
    transport_type: str
    target: str | None
    auth_status: str | None


@dataclass(slots=True)
class McpServerDetail:
    name: str
    enabled: bool
    disabled_reason: str | None
    transport_type: str
    url: str | None
    command: str | None
    args: list[str]
    cwd: str | None
    bearer_token_env_var: str | None
    auth_status: str | None
    startup_timeout_sec: int | None
    tool_timeout_sec: int | None
    enabled_tools: list[str]
    disabled_tools: list[str]


@dataclass(slots=True)
class McpListResult:
    ok: bool
    summary: str
    servers: list[McpServerSummary]
    stdout: str
    stderr: str
    command: list[str] | None


@dataclass(slots=True)
class McpDetailResult:
    ok: bool
    summary: str
    detail: McpServerDetail | None
    stdout: str
    stderr: str
    command: list[str] | None


class McpService:
    """Read configured Codex MCP servers without exposing sensitive values."""

    def __init__(self, *, codex_bin: str, timeout_seconds: int) -> None:
        self.codex_bin = codex_bin
        self.timeout_seconds = timeout_seconds
        self._name_pattern = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

    def list_servers(self) -> McpListResult:
        command = [self.codex_bin, "mcp", "list", "--json"]
        proc = self._run(command)
        if proc.returncode != 0:
            error = self._clean_cli_message(proc.stderr, proc.stdout) or "读取 MCP 列表失败。"
            return McpListResult(
                ok=False,
                summary=self._shorten(error),
                servers=[],
                stdout=proc.stdout,
                stderr=proc.stderr,
                command=command,
            )

        payload = self._parse_json_payload(proc.stdout)
        if not isinstance(payload, list):
            return McpListResult(
                ok=False,
                summary="MCP 列表输出解析失败。",
                servers=[],
                stdout=proc.stdout,
                stderr=proc.stderr,
                command=command,
            )

        servers: list[McpServerSummary] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            servers.append(self._to_summary(item))
        servers.sort(key=lambda item: item.name.lower())
        return McpListResult(
            ok=True,
            summary=f"已读取 {len(servers)} 个 MCP 服务。",
            servers=servers,
            stdout=proc.stdout,
            stderr=proc.stderr,
            command=command,
        )

    def get_server(self, name: str) -> McpDetailResult:
        normalized = name.strip()
        if not normalized:
            return McpDetailResult(
                ok=False,
                summary="用法: /mcp [name]",
                detail=None,
                stdout="",
                stderr="",
                command=None,
            )
        if not self._name_pattern.fullmatch(normalized):
            return McpDetailResult(
                ok=False,
                summary="MCP 名称不合法。",
                detail=None,
                stdout="",
                stderr="",
                command=None,
            )

        command = [self.codex_bin, "mcp", "get", normalized, "--json"]
        proc = self._run(command)
        if proc.returncode != 0:
            error = self._clean_cli_message(proc.stderr, proc.stdout) or "读取 MCP 详情失败。"
            return McpDetailResult(
                ok=False,
                summary=self._shorten(error),
                detail=None,
                stdout=proc.stdout,
                stderr=proc.stderr,
                command=command,
            )

        payload = self._parse_json_payload(proc.stdout)
        if not isinstance(payload, dict):
            return McpDetailResult(
                ok=False,
                summary="MCP 详情输出解析失败。",
                detail=None,
                stdout=proc.stdout,
                stderr=proc.stderr,
                command=command,
            )

        return McpDetailResult(
            ok=True,
            summary=f"已读取 MCP 详情: {normalized}",
            detail=self._to_detail(payload),
            stdout=proc.stdout,
            stderr=proc.stderr,
            command=command,
        )

    def _to_summary(self, payload: dict[str, Any]) -> McpServerSummary:
        transport = payload.get("transport")
        transport_info = transport if isinstance(transport, dict) else {}
        transport_type = str(transport_info.get("type") or "unknown")
        target: str | None = None
        if transport_type in {"streamable_http", "sse"}:
            url = transport_info.get("url")
            target = redact_text(str(url)) if url else None
        elif transport_type == "stdio":
            command = transport_info.get("command")
            target = str(command) if command else None

        auth_status = payload.get("auth_status")
        auth_text = str(auth_status) if auth_status is not None else None
        return McpServerSummary(
            name=str(payload.get("name") or "unknown"),
            enabled=bool(payload.get("enabled")),
            transport_type=transport_type,
            target=target,
            auth_status=auth_text,
        )

    def _to_detail(self, payload: dict[str, Any]) -> McpServerDetail:
        transport = payload.get("transport")
        transport_info = transport if isinstance(transport, dict) else {}
        transport_type = str(transport_info.get("type") or "unknown")
        args_raw = transport_info.get("args")
        args = [str(item) for item in args_raw] if isinstance(args_raw, list) else []
        enabled_tools = self._to_string_list(payload.get("enabled_tools"))
        disabled_tools = self._to_string_list(payload.get("disabled_tools"))
        startup_timeout = payload.get("startup_timeout_sec")
        tool_timeout = payload.get("tool_timeout_sec")
        auth_status = payload.get("auth_status")
        disabled_reason = payload.get("disabled_reason")

        url = transport_info.get("url")
        command = transport_info.get("command")
        cwd = transport_info.get("cwd")
        bearer_token_env_var = transport_info.get("bearer_token_env_var")

        return McpServerDetail(
            name=str(payload.get("name") or "unknown"),
            enabled=bool(payload.get("enabled")),
            disabled_reason=str(disabled_reason) if disabled_reason is not None else None,
            transport_type=transport_type,
            url=redact_text(str(url)) if url else None,
            command=str(command) if command else None,
            args=args,
            cwd=str(cwd) if cwd else None,
            bearer_token_env_var=str(bearer_token_env_var) if bearer_token_env_var else None,
            auth_status=str(auth_status) if auth_status is not None else None,
            startup_timeout_sec=int(startup_timeout) if isinstance(startup_timeout, int) else None,
            tool_timeout_sec=int(tool_timeout) if isinstance(tool_timeout, int) else None,
            enabled_tools=enabled_tools,
            disabled_tools=disabled_tools,
        )

    def _to_string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                args=command,
                returncode=127,
                stdout="",
                stderr=f"Codex binary not found: {self.codex_bin}",
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                args=command,
                returncode=124,
                stdout="",
                stderr=f"MCP command timed out after {self.timeout_seconds}s",
            )

    def _parse_json_payload(self, stdout: str) -> Any:
        text = stdout.strip()
        if not text:
            return None

        object_start = text.find("{")
        list_start = text.find("[")
        starts = [idx for idx in (object_start, list_start) if idx >= 0]
        if not starts:
            return None
        start_index = min(starts)
        json_part = text[start_index:]
        try:
            return json.loads(json_part)
        except json.JSONDecodeError:
            return None

    def _shorten(self, text: str, limit: int = 220) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _clean_cli_message(self, stderr: str, stdout: str) -> str:
        combined = (stderr or "").strip() or (stdout or "").strip()
        if not combined:
            return ""
        lines = [line for line in combined.splitlines() if line.strip()]
        filtered = [line for line in lines if not line.lstrip().startswith("WARNING: proceeding")]
        cleaned = "\n".join(filtered if filtered else lines)
        return self._shorten(cleaned)
