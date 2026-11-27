"""Verify the equipment MCP server over Streamable HTTP."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
from mcp.types import CallToolResult, Tool

from mcp_lab.server.app import EquipmentTools, create_server
from mcp_lab.server.database import initialize_database


@pytest.fixture
def equipment_client(tmp_path: Path, mcp_http_client):
    db_path = tmp_path / "equipment.sqlite3"
    sql_dir = Path(__file__).resolve().parents[2] / "data"
    initialize_database(db_path, sql_dir=sql_dir)
    with closing(sqlite3.connect(db_path)) as database, database:
        database.execute("PRAGMA foreign_keys = ON")
        database.executemany(
            """
            INSERT INTO employees (employee_id, role, start_date, equipment_history_status)
            VALUES (?, ?, ?, ?)
            """,
            (
                ("EMP-900", "manager", "2022-08-15", "complete"),
                ("EMP-901", "manager", "2022-08-15", "complete"),
            ),
        )
        database.execute(
            """
            INSERT INTO equipment_assets (asset_id, employee_id, item, issued_on, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("LAP-900", "EMP-900", "laptop", "2024-02-06", "active"),
        )
        database.executemany(
            """
            INSERT INTO equipment_policies
                (role, item, disposition, max_active_quantity,
                 replacement_interval_years)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                ("manager", "laptop", "allowed", 1, 5),
                ("manager", "gaming_console", "prohibited", None, None),
            ),
        )

    server = create_server(EquipmentTools(db_path, today=lambda: date(2026, 8, 14)))
    return lambda: mcp_http_client(server)


async def _call(connect, name: str, arguments: dict) -> CallToolResult:
    async with asyncio.timeout(10), connect() as client:
        return await client.call_tool(name, arguments)


def _successful_content(response: CallToolResult) -> dict:
    if response.is_error or response.structured_content is None:
        pytest.fail(
            f"Expected a successful structured tool response. Received {response.content}"
        )
    return response.structured_content


@pytest.fixture
def equipment_tools(equipment_client) -> list[Tool]:
    async def discover() -> list[Tool]:
        async with asyncio.timeout(10), equipment_client() as client:
            return (await client.list_tools()).tools

    return asyncio.run(discover())


def test_equipment_server_exposes_four_tools(equipment_tools: list[Tool]) -> None:
    assert {tool.name for tool in equipment_tools} == {
        "get_employee_info",
        "get_policy_limits",
        "check_request_eligibility",
        "flag_for_human_review",
    }


def test_review_request_id_is_required(equipment_tools: list[Tool]) -> None:
    review_tool = next(
        tool for tool in equipment_tools if tool.name == "flag_for_human_review"
    )
    assert "request_id" in review_tool.input_schema["required"]


def test_equipment_server_exposes_employee_lookup(equipment_client) -> None:
    response = asyncio.run(
        _call(equipment_client, "get_employee_info", {"employee_id": "EMP-900"})
    )

    assert _successful_content(response) == {
        "employee_id": "EMP-900",
        "role": "manager",
        "tenure_years": 3,
        "current_equipment": [
            {
                "asset_id": "LAP-900",
                "item": "laptop",
                "issued_on": "2024-02-06",
                "status": "active",
            }
        ],
    }


def test_equipment_server_exposes_policy_lookup(equipment_client) -> None:
    response = asyncio.run(
        _call(equipment_client, "get_policy_limits", {"role": "manager"})
    )

    assert _successful_content(response) == {
        "laptop": {
            "disposition": "allowed",
            "max_active_quantity": 1,
            "replacement_interval_years": 5,
        },
        "gaming_console": {
            "disposition": "prohibited",
            "max_active_quantity": None,
            "replacement_interval_years": None,
        },
    }


@pytest.mark.parametrize(
    ("employee_id", "item", "expected"),
    [
        ("EMP-901", "laptop", "approved"),
        ("EMP-901", "Gaming console", "denied"),
        ("EMP-999", "laptop", "escalated"),
    ],
    ids=["first-issue", "prohibited-equipment", "unknown-employee"],
)
def test_equipment_server_evaluates_requests(
    equipment_client,
    employee_id: str,
    item: str,
    expected: str,
) -> None:
    arguments = {
        "employee_id": employee_id,
        "item": item,
        "request_kind": "first_issue",
    }

    response = asyncio.run(
        _call(equipment_client, "check_request_eligibility", arguments)
    )

    assert _successful_content(response)["outcome"] == expected


@pytest.fixture
def review_receipts(equipment_client) -> tuple[dict, dict]:
    arguments = {
        "employee_id": "EMP-999",
        "request": "I need a work laptop",
        "reason": "The employee could not be verified.",
        "request_id": "REQ-HTTP-001",
    }

    async def record_and_retry() -> tuple[dict, dict]:
        first = await _call(equipment_client, "flag_for_human_review", arguments)
        retry = await _call(equipment_client, "flag_for_human_review", arguments)
        return _successful_content(first), _successful_content(retry)

    return asyncio.run(record_and_retry())


def test_equipment_server_returns_original_review_receipt(
    review_receipts: tuple[dict, dict],
) -> None:
    first, retry = review_receipts
    assert retry == first


def test_equipment_server_records_review_once(
    review_receipts: tuple[dict, dict],
    tmp_path: Path,
) -> None:
    receipt, _ = review_receipts

    with closing(sqlite3.connect(tmp_path / "equipment.sqlite3")) as database:
        rows = database.execute(
            "SELECT review_id, request_id, employee_id, request_text, reason FROM human_reviews"
        ).fetchall()

    assert rows == [
        (
            receipt["review_id"],
            "REQ-HTTP-001",
            "EMP-999",
            "I need a work laptop",
            "The employee could not be verified.",
        )
    ]


@pytest.mark.parametrize(
    ("request_id", "message"),
    [({}, "request_id"), ({"request_id": " "}, "request ID")],
    ids=["missing", "blank"],
)
def test_invalid_review_call_is_rejected_over_http(
    equipment_client, tmp_path: Path, request_id: dict, message: str
) -> None:
    response = asyncio.run(
        _call(
            equipment_client,
            "flag_for_human_review",
            {
                "employee_id": "EMP-900",
                "request": "Laptop",
                "reason": "Needs review",
                **request_id,
            },
        )
    )

    with closing(sqlite3.connect(tmp_path / "equipment.sqlite3")) as db:
        pending = db.execute("SELECT count(*) FROM human_reviews").fetchone()[0]

    error_text = " ".join(
        block.text for block in response.content if block.type == "text"
    )
    assert (response.is_error, message in error_text, pending) == (True, True, 0)


def test_concurrent_identical_review_calls_commit_once(
    equipment_client, tmp_path: Path
) -> None:
    async def submit_both() -> tuple[dict, dict]:
        arguments = {
            "employee_id": "EMP-900",
            "request": "Laptop",
            "reason": "Needs review",
            "request_id": "REQ-CONCURRENT",
        }
        async with asyncio.timeout(10), equipment_client() as client:
            first, second = await asyncio.gather(
                client.call_tool("flag_for_human_review", arguments),
                client.call_tool("flag_for_human_review", arguments),
            )
        return _successful_content(first), _successful_content(second)

    first, second = asyncio.run(submit_both())
    with closing(sqlite3.connect(tmp_path / "equipment.sqlite3")) as db:
        rows = db.execute("SELECT request_id, review_id FROM human_reviews").fetchall()

    assert (second, rows) == (first, [("REQ-CONCURRENT", first["review_id"])])


@pytest.mark.socket
def test_equipment_server_and_cli_entry_points_over_tcp(
    serve_module, tmp_path: Path
) -> None:
    db_path = tmp_path / "equipment.sqlite3"
    initialize_database(db_path, seed=True)

    with serve_module("mcp_lab.server.app", MCP_DB_PATH=str(db_path)) as url:
        agent = subprocess.run(
            [sys.executable, "-m", "mcp_lab.agent", "--url", url],
            capture_output=True,
            text=True,
            timeout=15,
        )
        client = subprocess.run(
            [
                sys.executable,
                "-m",
                "mcp_lab.client",
                "--url",
                url,
                "get_employee_info",
                '{"employee_id": "EMP-999"}',
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

    assert (
        agent.returncode,
        json.loads(agent.stdout)["employee"]["employee_id"],
        client.returncode,
        "Unknown employee ID" in client.stderr,
    ) == (0, "EMP-001", 1, True)
