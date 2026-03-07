"""Safe wrappers for reading Codex MCP configuration."""

from dataclasses import dataclass
import json
from pathlib import Path
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


@dataclass(slots=True)
class McpToggleResult:
    ok: bool
    summary: str
    name: str
    enabled: bool
    config_path: str | None


class McpService:
    """Read configured Codex MCP servers without exposing sensitive values."""

    _SECTION_PATTERN = re.compile(
        r"^\s*(?P<comment>#\s*)?\[(?P<section>mcp_servers\.[^\]]+)\]\s*$"
    )

    def __init__(self, *, codex_bin: str, timeout_seconds: int, config_path: Path | None = None) -> None:
        self.codex_bin = codex_bin
        self.timeout_seconds = timeout_seconds
        self.config_path = config_path
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
        configured = self._load_configured_servers()
        known = {item.name for item in servers}
        for name, enabled in configured.items():
            if name in known:
                continue
            servers.append(
                McpServerSummary(
                    name=name,
                    enabled=enabled,
                    transport_type="configured",
                    target=None,
                    auth_status=None,
                )
            )
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
            fallback = self._detail_from_config(normalized)
            if fallback is not None:
                return McpDetailResult(
                    ok=True,
                    summary=f"已读取 MCP 详情: {normalized}",
                    detail=fallback,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    command=command,
                )
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

    def set_server_enabled(self, name: str, *, enabled: bool) -> McpToggleResult:
        normalized = name.strip()
        if not normalized or not self._name_pattern.fullmatch(normalized):
            return McpToggleResult(
                ok=False,
                summary="MCP 名称不合法。",
                name=normalized or name,
                enabled=enabled,
                config_path=str(self.config_path) if self.config_path else None,
            )
        if self.config_path is None:
            return McpToggleResult(
                ok=False,
                summary="未配置 Codex config.toml 路径。",
                name=normalized,
                enabled=enabled,
                config_path=None,
            )
        if not self.config_path.exists():
            return McpToggleResult(
                ok=False,
                summary="Codex config.toml 不存在。",
                name=normalized,
                enabled=enabled,
                config_path=str(self.config_path),
            )

        original = self.config_path.read_text(encoding="utf-8")
        updated = self._set_block_enabled(original, normalized, enabled=enabled)
        if updated is None:
            return McpToggleResult(
                ok=False,
                summary=f"未找到 MCP 配置段: {normalized}",
                name=normalized,
                enabled=enabled,
                config_path=str(self.config_path),
            )
        if updated != original:
            self.config_path.write_text(updated, encoding="utf-8")
        action = "启用" if enabled else "停用"
        return McpToggleResult(
            ok=True,
            summary=f"已{action} MCP: {normalized}",
            name=normalized,
            enabled=enabled,
            config_path=str(self.config_path),
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

    def _load_configured_servers(self) -> dict[str, bool]:
        if self.config_path is None or not self.config_path.exists():
            return {}
        mapping: dict[str, bool] = {}
        for line in self.config_path.read_text(encoding="utf-8").splitlines():
            match = self._SECTION_PATTERN.match(line)
            if not match:
                continue
            root_name = self._root_name_from_section(match.group("section"))
            if root_name is None:
                continue
            mapping[root_name] = match.group("comment") is None
        return mapping

    def _detail_from_config(self, name: str) -> McpServerDetail | None:
        if self.config_path is None or not self.config_path.exists():
            return None
        block = self._extract_block(self.config_path.read_text(encoding="utf-8"), name)
        if block is None:
            return None
        enabled, lines = block
        command = self._extract_assignment(lines, "command")
        url = self._extract_assignment(lines, "url")
        cwd = self._extract_assignment(lines, "cwd")
        args = self._extract_array_assignment(lines, "args")
        transport_type = "unknown"
        if url:
            transport_type = "streamable_http"
        elif command:
            transport_type = "stdio"
        return McpServerDetail(
            name=name,
            enabled=enabled,
            disabled_reason=None if enabled else "配置段已注释",
            transport_type=transport_type,
            url=redact_text(url) if url else None,
            command=command,
            args=args,
            cwd=cwd,
            bearer_token_env_var=None,
            auth_status=None,
            startup_timeout_sec=None,
            tool_timeout_sec=None,
            enabled_tools=[],
            disabled_tools=[],
        )

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

    def _extract_block(self, text: str, name: str) -> tuple[bool, list[str]] | None:
        current_name: str | None = None
        current_enabled = False
        captured: list[str] = []

        for line in text.splitlines():
            match = self._SECTION_PATTERN.match(line)
            if match:
                section_name = self._root_name_from_section(match.group("section"))
                if section_name is None:
                    continue
                section_enabled = match.group("comment") is None
                if current_name == name and section_name != name:
                    return current_enabled, captured
                current_name = section_name
                current_enabled = section_enabled
                captured = [line] if section_name == name else []
                continue
            if current_name == name:
                captured.append(line)

        if current_name == name and captured:
            return current_enabled, captured
        return None

    def _set_block_enabled(self, text: str, name: str, *, enabled: bool) -> str | None:
        lines = text.splitlines()
        result: list[str] = []
        current_name: str | None = None
        found = False

        for line in lines:
            match = self._SECTION_PATTERN.match(line)
            if match:
                current_name = self._root_name_from_section(match.group("section"))
            if current_name == name:
                found = True
                result.append(self._uncomment_line(line) if enabled else self._comment_line(line))
            else:
                result.append(line)
        if not found:
            return None
        return "\n".join(result) + ("\n" if text.endswith("\n") else "")

    def _comment_line(self, line: str) -> str:
        if not line.strip():
            return line
        stripped = line.lstrip()
        if stripped.startswith("#"):
            return line
        indent = line[: len(line) - len(stripped)]
        return f"{indent}# {stripped}"

    def _uncomment_line(self, line: str) -> str:
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            return line
        indent = line[: len(line) - len(stripped)]
        uncommented = stripped[1:]
        if uncommented.startswith(" "):
            uncommented = uncommented[1:]
        return f"{indent}{uncommented}"

    def _extract_assignment(self, lines: list[str], key: str) -> str | None:
        pattern = re.compile(rf"^\s*(?:#\s*)?{re.escape(key)}\s*=\s*\"([^\"]+)\"")
        for line in lines:
            match = pattern.match(line)
            if match:
                return match.group(1)
        return None

    def _extract_array_assignment(self, lines: list[str], key: str) -> list[str]:
        pattern = re.compile(rf"^\s*(?:#\s*)?{re.escape(key)}\s*=\s*\[(.*)\]\s*$")
        for line in lines:
            match = pattern.match(line)
            if not match:
                continue
            raw = match.group(1).strip()
            if not raw:
                return []
            return [item.strip().strip('"').strip("'") for item in raw.split(",") if item.strip()]
        return []

    def _root_name_from_section(self, section: str) -> str | None:
        if not section.startswith("mcp_servers."):
            return None
        remainder = section[len("mcp_servers.") :]
        if not remainder:
            return None
        return remainder.split(".", 1)[0]
