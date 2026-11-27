"""Verify manual MCP argument handling and command output."""

import json
import sys
from unittest.mock import MagicMock

import pytest
from mcp.types import CallToolResult, TextContent

from mcp_lab import client


@pytest.mark.parametrize("field", ["employee_id", "request", "reason"])
def test_prepare_review_rejects_blank_field(field: str) -> None:
    payload = {"employee_id": "EMP-001", "request": "Laptop", "reason": "Check policy"}
    payload[field] = " "

    with pytest.raises(ValueError, match="nonempty strings"):
        client.prepare_review(**payload)


def test_prepare_review_assigns_distinct_ids_to_new_submissions() -> None:
    original = client.prepare_review("EMP-001", "Laptop", "Check policy")
    separate = client.prepare_review("EMP-001", "Laptop", "Check policy")

    assert separate["request_id"] != original["request_id"]


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("get_employee_info", "{", "Invalid JSON arguments"),
        ("get_employee_info", "[]", "must be a JSON object"),
        ("prepare-review", "{}", "missing"),
    ],
    ids=["invalid-json", "non-object", "missing-review-fields"],
)
def test_client_reports_invalid_arguments(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    tool: str,
    arguments: str,
    message: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["client", tool, arguments])

    with pytest.raises(SystemExit) as exit_info:
        client.main()

    assert (exit_info.value.code, message in capsys.readouterr().err) == (2, True)


def test_prepare_review_cli_prints_reusable_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    inputs = {"employee_id": "EMP-001", "request": "Laptop", "reason": "Check policy"}
    monkeypatch.setattr(sys, "argv", ["client", "prepare-review", json.dumps(inputs)])
    monkeypatch.setattr(client, "uuid4", lambda: MagicMock(hex="fixed"))

    status = client.main()

    assert (status, json.loads(capsys.readouterr().out)) == (
        0,
        inputs | {"request_id": "REQ-fixed"},
    )


def test_client_entry_point_prints_structured_tool_output(
    cli_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    payload = {"employee_id": "EMP-001"}
    cli_client.call_tool.return_value = CallToolResult(
        content=[], structured_content=payload
    )
    monkeypatch.setattr(
        sys, "argv", ["client", "get_employee_info", json.dumps(payload)]
    )

    monkeypatch.setattr(client, "Client", lambda url: cli_client)

    status = client.main()

    assert (status, json.loads(capsys.readouterr().out)) == (0, payload)


def test_client_prints_tool_error_to_stderr(
    cli_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(client, "Client", lambda url: cli_client)
    monkeypatch.setattr(sys, "argv", ["client", "get_employee_info"])
    error = TextContent(type="text", text="Unknown employee")
    cli_client.call_tool.return_value = CallToolResult(content=[error], isError=True)

    status = client.main()

    assert (status, json.loads(capsys.readouterr().err)) == (
        1,
        [error.model_dump(mode="json")],
    )


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (TimeoutError(), "MCP call timed out"),
        (RuntimeError("offline"), "MCP call failed: offline"),
    ],
    ids=["timeout", "connection-error"],
)
def test_client_reports_transport_failure(
    cli_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    failure: Exception,
    message: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["client", "get_employee_info"])
    monkeypatch.setattr(client, "Client", lambda url: cli_client)
    cli_client.call_tool.side_effect = failure

    status = client.main()

    assert (status, message in capsys.readouterr().err) == (1, True)
