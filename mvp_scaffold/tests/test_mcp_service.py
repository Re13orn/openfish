import subprocess

from src.mcp_service import McpService


def _service() -> McpService:
    return McpService(codex_bin="codex", timeout_seconds=30)


def test_list_servers_parses_json_with_warning_prefix(monkeypatch) -> None:
    svc = _service()
    payload = (
        "WARNING: proceeding\n"
        '[{"name":"playwright","enabled":true,"auth_status":"unsupported",'
        '"transport":{"type":"stdio","command":"npx","args":["@playwright/mcp@latest"]}}]'
    )

    monkeypatch.setattr(
        svc,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 0, payload, ""),
    )

    result = svc.list_servers()

    assert result.ok is True
    assert len(result.servers) == 1
    assert result.servers[0].name == "playwright"
    assert result.servers[0].transport_type == "stdio"
    assert result.servers[0].target == "npx"


def test_get_server_ignores_sensitive_header_values(monkeypatch) -> None:
    svc = _service()
    payload = (
        '{'
        '"name":"grapefruit","enabled":true,'
        '"transport":{"type":"streamable_http","url":"http://localhost:31337/api/mcp",'
        '"http_headers":{"Authorization":"Bearer SECRET123"}}'
        "}"
    )

    monkeypatch.setattr(
        svc,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 0, payload, ""),
    )

    result = svc.get_server("grapefruit")

    assert result.ok is True
    assert result.detail is not None
    assert result.detail.name == "grapefruit"
    assert result.detail.url == "http://localhost:31337/api/mcp"
    assert "SECRET123" not in repr(result.detail)


def test_get_server_rejects_invalid_name() -> None:
    svc = _service()
    result = svc.get_server("invalid name")
    assert result.ok is False
    assert "不合法" in result.summary
