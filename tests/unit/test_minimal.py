"""Verify the minimal MCP sample through its entry points."""

import asyncio
from unittest.mock import MagicMock, Mock

import pytest
from mcp.server import MCPServer
from mcp.types import CallToolResult, ListToolsResult, Tool

from mcp_lab.minimal import sampleclient, sampleserver


def test_sample_ping_returns_pong() -> None:
    assert sampleserver.ping() == "pong"


def test_sample_client_verifies_ping(
    cli_client: MagicMock,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_client.list_tools.return_value = ListToolsResult(
        tools=[Tool(name="ping", inputSchema={"type": "object"})]
    )
    cli_client.call_tool.return_value = CallToolResult(
        content=[], structured_content={"result": "pong"}
    )

    monkeypatch.setattr(sampleclient, "Client", lambda url: cli_client)

    asyncio.run(sampleclient.main())

    assert "MCP connection and tool call verified." in capsys.readouterr().out


def test_sample_server_uses_default_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr(MCPServer, "run", run)
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)

    sampleserver.main()

    run.assert_called_once_with(
        transport="streamable-http", host="127.0.0.1", port=8000
    )
