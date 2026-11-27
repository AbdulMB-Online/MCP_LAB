"""Exercise MCP tool functions and server configuration without binding a port."""

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_lab.server import app
from mcp_lab.server.database import initialize_database


@pytest.fixture
def server_database(tmp_path: Path) -> Path:
    path = tmp_path / "equipment.sqlite3"
    initialize_database(path, seed=True)
    return path


@pytest.fixture
def tools(server_database: Path) -> app.EquipmentTools:
    return app.EquipmentTools(server_database)


@pytest.fixture
def invalid_record(server_database: Path) -> str:
    employee_id = "EMP-001"
    with closing(sqlite3.connect(server_database)) as db, db:
        db.execute(
            "UPDATE employees SET start_date = 'invalid' WHERE employee_id = ?",
            (employee_id,),
        )
    return employee_id


def test_employee_tool_returns_directory_role(tools: app.EquipmentTools) -> None:
    record = tools.get_employee_info("EMP-001")
    assert record.structured_content["role"] == "standard"


def test_employee_tool_reports_missing_record(tools: app.EquipmentTools) -> None:
    result = tools.get_employee_info("EMP-999")

    assert (result.is_error, result.meta) == (
        True,
        {"equipment_error": "employee_not_found"},
    )


def test_employee_tool_reports_invalid_date(
    tools: app.EquipmentTools, invalid_record: str
) -> None:
    result = tools.get_employee_info(invalid_record)

    assert (result.is_error, result.meta) == (
        True,
        {"equipment_error": "invalid_employee_dates"},
    )


def test_eligibility_tool_escalates_invalid_date(
    tools: app.EquipmentTools, invalid_record: str
) -> None:
    decision = tools.check_request_eligibility(invalid_record, "laptop", "first_issue")
    assert decision == {
        "outcome": "escalated",
        "reason": "The employee record contains an invalid date.",
    }


@pytest.mark.parametrize(
    ("employee_id", "outcome"),
    [("EMP-001", "approved"), ("EMP-999", "escalated")],
)
def test_eligibility_tool_uses_employee_directory(
    tools: app.EquipmentTools,
    employee_id: str,
    outcome: str,
) -> None:
    decision = tools.check_request_eligibility(employee_id, "laptop", "first_issue")
    assert decision["outcome"] == outcome


def test_policy_tool_reads_role_limits(tools: app.EquipmentTools) -> None:
    limits = tools.get_policy_limits("manager")
    assert limits.structured_content["monitor"]["max_active_quantity"] == 2


def test_policy_tool_reports_unknown_role(tools: app.EquipmentTools) -> None:
    result = tools.get_policy_limits("unknown")

    assert (result.is_error, result.meta) == (
        True,
        {"equipment_error": "policy_not_found"},
    )


def test_review_tool_returns_persisted_request_id(tools: app.EquipmentTools) -> None:
    receipt = tools.flag_for_human_review("EMP-001", "Laptop", "Check", "REQ-001")

    assert receipt["request_id"] == "REQ-001"


def test_review_tool_reports_invalid_request_id(tools: app.EquipmentTools) -> None:
    with pytest.raises(ToolError, match="request ID is required"):
        tools.flag_for_human_review("EMP-001", "Laptop", "Check", " ")


def test_server_entry_point_uses_configured_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Mock()
    monkeypatch.setattr(MCPServer, "run", run)
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8200")

    app.main()

    run.assert_called_once_with(
        transport="streamable-http", host="127.0.0.1", port=8200
    )


@pytest.fixture
def future_asset_with_unknown_history(server_database: Path) -> str:
    with closing(sqlite3.connect(server_database)) as db, db:
        db.execute(
            "INSERT INTO equipment_assets VALUES (?, ?, ?, ?, ?)",
            ("MON-FUT", "EMP-003", "monitor", "9990-01-01", "active"),
        )
    return "EMP-003"


def test_employee_lookup_rejects_future_asset_in_partial_history(
    tools: app.EquipmentTools,
    future_asset_with_unknown_history: str,
) -> None:
    result = tools.get_employee_info(future_asset_with_unknown_history)

    assert (result.is_error, result.meta) == (
        True,
        {"equipment_error": "invalid_employee_dates"},
    )


def test_eligibility_checks_partial_history_before_console_prohibition(
    tools: app.EquipmentTools,
    future_asset_with_unknown_history: str,
) -> None:
    decision = tools.check_request_eligibility(
        future_asset_with_unknown_history, "gaming console"
    )

    assert decision == {
        "outcome": "escalated",
        "reason": "The equipment history contains a future issue date.",
    }
