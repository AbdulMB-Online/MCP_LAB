"""Verify the equipment MCP server over Streamable HTTP."""

import asyncio
import os
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from mcp import Client

from mcp_lab.server.database import initialize_database


@pytest.fixture
def equipment_server_url(tmp_path: Path) -> Iterator[str]:
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

    host = "127.0.0.1"
    with socket.socket() as port_probe:
        port_probe.bind((host, 0))
        port = port_probe.getsockname()[1]

    environment = {
        **os.environ,
        "MCP_HOST": host,
        "MCP_PORT": str(port),
        "MCP_DB_PATH": str(db_path),
    }
    log_path = tmp_path / "equipment-server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "mcp_lab.server.app"],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    with socket.create_connection((host, port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail(f"Equipment server did not start:\n{log_path.read_text()}")

            yield f"http://{host}:{port}/mcp"
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def test_equipment_server_exposes_employee_lookup(equipment_server_url: str) -> None:
    """Discover the employee tool and read a record from the configured database."""

    async def check_lookup() -> None:
        async with asyncio.timeout(10):
            async with Client(equipment_server_url) as client:
                tools = await client.list_tools()
                assert "get_employee_info" in {tool.name for tool in tools.tools}

                response = await client.call_tool(
                    "get_employee_info", {"employee_id": "EMP-900"}
                )
                assert not response.is_error
                record = response.structured_content
                assert record is not None
                assert record["employee_id"] == "EMP-900"
                assert record["role"] == "manager"
                assert type(record["tenure_years"]) is int
                assert record["tenure_years"] >= 0
                assert record["current_equipment"] == [
                    {
                        "asset_id": "LAP-900",
                        "item": "laptop",
                        "issued_on": "2024-02-06",
                        "status": "active",
                    }
                ]

    asyncio.run(check_lookup())


def test_equipment_server_exposes_policy_lookup(equipment_server_url: str) -> None:
    """Discover both tools and read a policy from the configured database."""

    async def check_policy() -> None:
        async with asyncio.timeout(10):
            async with Client(equipment_server_url) as client:
                tools = await client.list_tools()
                assert {"get_employee_info", "get_policy_limits"} <= {
                    tool.name for tool in tools.tools
                }

                response = await client.call_tool(
                    "get_policy_limits", {"role": "manager"}
                )
                assert not response.is_error
                assert response.structured_content == {
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

    asyncio.run(check_policy())


def test_equipment_server_evaluates_requests(equipment_server_url: str) -> None:
    async def check_eligibility() -> None:
        async with asyncio.timeout(10), Client(equipment_server_url) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "get_employee_info",
                "get_policy_limits",
                "check_request_eligibility",
                "flag_for_human_review",
            }
            for employee_id, item, outcome in (
                ("EMP-901", "laptop", "approved"),
                ("EMP-901", "Gaming console", "denied"),
                ("EMP-999", "laptop", "escalated"),
            ):
                response = await client.call_tool(
                    "check_request_eligibility",
                    {
                        "employee_id": employee_id,
                        "item": item,
                        "request_kind": "first_issue",
                    },
                )
                assert not response.is_error
                decision = response.structured_content
                assert decision is not None
                assert decision["outcome"] == outcome
                assert decision["reason"]

    asyncio.run(check_eligibility())


def test_equipment_server_records_review_once(
    equipment_server_url: str, tmp_path: Path
) -> None:
    async def record_review() -> str:
        arguments = {
            "employee_id": "EMP-999",
            "request": "I need a work laptop",
            "reason": "The employee could not be verified.",
            "request_id": "REQ-HTTP-001",
        }
        async with asyncio.timeout(10), Client(equipment_server_url) as client:
            tools = (await client.list_tools()).tools
            review_tool = next(
                tool for tool in tools if tool.name == "flag_for_human_review"
            )
            assert "request_id" in review_tool.input_schema["required"]
            first = await client.call_tool("flag_for_human_review", arguments)
            retry = await client.call_tool("flag_for_human_review", arguments)
            assert not first.is_error and not retry.is_error
            receipt = first.structured_content
            assert receipt is not None
            assert retry.structured_content == receipt
            assert receipt["outcome"] == "escalated"
            assert receipt["request_id"] == arguments["request_id"]
            return receipt["review_id"]

    review_id = asyncio.run(record_review())
    with closing(sqlite3.connect(tmp_path / "equipment.sqlite3")) as database:
        rows = database.execute(
            "SELECT review_id, employee_id, request_text, reason FROM human_reviews"
        ).fetchall()
    assert rows == [
        (
            review_id,
            "EMP-999",
            "I need a work laptop",
            "The employee could not be verified.",
        )
    ]
