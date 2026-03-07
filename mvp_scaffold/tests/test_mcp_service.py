import subprocess
from pathlib import Path

from src.mcp_service import McpService


def _service(config_path: Path | None = None) -> McpService:
    return McpService(codex_bin="codex", timeout_seconds=30, config_path=config_path)


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


def test_list_servers_merges_commented_configured_server(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[mcp_servers.playwright]\ncommand = "npx"\n# [mcp_servers.android-control]\n# command = "node"\n',
        encoding="utf-8",
    )
    svc = _service(config_path)
    payload = '[{"name":"playwright","enabled":true,"transport":{"type":"stdio","command":"npx"}}]'
    monkeypatch.setattr(
        svc,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 0, payload, ""),
    )

    result = svc.list_servers()

    assert result.ok is True
    names = {item.name: item.enabled for item in result.servers}
    assert names["playwright"] is True
    assert names["android-control"] is False


def test_get_server_falls_back_to_commented_config_block(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '# [mcp_servers.android-control]\n# command = "node"\n# args = ["server.js"]\n# cwd = "/tmp/demo"\n',
        encoding="utf-8",
    )
    svc = _service(config_path)
    monkeypatch.setattr(
        svc,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 1, "", "not found"),
    )

    result = svc.get_server("android-control")

    assert result.ok is True
    assert result.detail is not None
    assert result.detail.enabled is False
    assert result.detail.command == "node"
    assert result.detail.args == ["server.js"]


def test_set_server_enabled_comments_and_uncomments_block(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[mcp_servers.playwright]\ncommand = "npx"\n\n[mcp_servers.playwright.http_headers]\nAuthorization = "Bearer x"\n',
        encoding="utf-8",
    )
    svc = _service(config_path)

    disabled = svc.set_server_enabled("playwright", enabled=False)
    after_disable = config_path.read_text(encoding="utf-8")
    enabled = svc.set_server_enabled("playwright", enabled=True)
    after_enable = config_path.read_text(encoding="utf-8")

    assert disabled.ok is True
    assert "# [mcp_servers.playwright]" in after_disable
    assert "# command = \"npx\"" in after_disable
    assert "# [mcp_servers.playwright.http_headers]" in after_disable
    assert enabled.ok is True
    assert "[mcp_servers.playwright]" in after_enable
    assert 'command = "npx"' in after_enable
    assert "[mcp_servers.playwright.http_headers]" in after_enable
