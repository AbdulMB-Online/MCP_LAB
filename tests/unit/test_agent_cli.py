"""Verify the agent's local and Foundry command entry points."""

import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx2 import HTTPStatusError, Request, Response
from mcp.types import CallToolResult, ListToolsResult, Tool

from mcp_lab import agent


def test_agent_entry_point_prints_first_request(
    cli_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    employee = {"employee_id": "EMP-001"}
    cli_client.call_tool.return_value = CallToolResult(
        content=[], structured_content=employee
    )
    monkeypatch.setattr(sys, "argv", ["agent"])

    monkeypatch.setattr(agent, "Client", lambda url: cli_client)

    status = agent.main()

    assert (status, json.loads(capsys.readouterr().out)) == (
        0,
        {"request": agent.REQUEST, "employee": employee},
    )


def test_foundry_cli_passes_employee_and_request(
    cli_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = MagicMock()
    monkeypatch.setattr(agent.FoundryModel, "from_environment", lambda http: model)
    monkeypatch.setattr(agent, "Client", lambda url: cli_client)
    loop = AsyncMock(return_value={"draft": "Policy response"})
    monkeypatch.setattr(agent, "run_react", loop)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent",
            "--foundry",
            "--employee-id",
            "EMP-002",
            "--request",
            "Laptop",
            "--request-id",
            "REQ-CLI",
        ],
    )

    agent.main()

    loop.assert_awaited_once_with(
        cli_client, model.complete, "EMP-002", "Laptop", request_id="REQ-CLI"
    )


def test_agent_reports_override_requires_foundry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(sys, "argv", ["agent", "--employee-id", "EMP-002"])

    with pytest.raises(SystemExit) as exit_info:
        agent.main()

    assert (exit_info.value.code, "requires --foundry" in capsys.readouterr().err) == (
        2,
        True,
    )


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (TimeoutError(), "Equipment request timed out"),
        (RuntimeError("offline"), "Equipment request failed: offline"),
    ],
    ids=["timeout", "connection-error"],
)
def test_agent_reports_transport_failure(
    cli_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    failure: Exception,
    message: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["agent"])
    monkeypatch.setattr(agent, "Client", lambda url: cli_client)
    cli_client.call_tool.side_effect = failure

    status = agent.main()

    assert (status, message in capsys.readouterr().err) == (1, True)


def test_foundry_failure_reports_the_intake_id(
    cli_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(sys, "argv", ["agent", "--foundry", "--request-id", "REQ-CLI"])
    monkeypatch.setattr(
        agent.FoundryModel, "from_environment", lambda http: MagicMock()
    )
    monkeypatch.setattr(agent, "Client", lambda url: cli_client)
    cli_client.list_tools.side_effect = TimeoutError

    status = agent.main()

    assert (status, capsys.readouterr().err.splitlines()) == (
        1,
        ["Equipment request timed out.", "Request ID: REQ-CLI"],
    )


@pytest.mark.parametrize(
    ("response_status", "body", "suffix"),
    [
        (401, None, ""),
        (
            400,
            {"error": {"message": "Unsupported parameter: parallel_tool_calls"}},
            (" Azure detail: Unsupported parameter: parallel_tool_calls"),
        ),
        (
            400,
            {"error": {"message": "The submitted key private-test-key is invalid."}},
            (" Azure detail: The submitted key [redacted] is invalid."),
        ),
    ],
    ids=["status-only", "provider-detail", "redacted-detail"],
)
def test_foundry_cli_reports_http_error_inside_task_group(
    cli_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    response_status: int,
    body: dict | None,
    suffix: str,
) -> None:
    url = "https://example.services.ai.azure.com/openai/v1/chat/completions"
    request = Request("POST", url, headers={"api-key": "private-test-key"})
    response = Response(response_status, json=body, request=request)
    failure = HTTPStatusError(
        "Foundry rejected the request", request=request, response=response
    )
    model = MagicMock()
    model.complete = AsyncMock(
        side_effect=ExceptionGroup(
            "unhandled errors in a TaskGroup", [ExceptionGroup("nested", [failure])]
        )
    )
    monkeypatch.setattr(agent.FoundryModel, "from_environment", lambda http: model)
    monkeypatch.setattr(agent, "Client", lambda url: cli_client)
    cli_client.list_tools.return_value = ListToolsResult(
        tools=[Tool(name="check_request_eligibility", inputSchema={"type": "object"})]
    )
    monkeypatch.setattr(sys, "argv", ["agent", "--foundry", "--request-id", "REQ-CLI"])

    status = agent.main()

    assert (status, capsys.readouterr().err.splitlines()) == (
        1,
        [
            f"Equipment request failed: HTTP {response_status} from example.services.ai.azure.com.{suffix}",
            "Request ID: REQ-CLI",
        ],
    )
