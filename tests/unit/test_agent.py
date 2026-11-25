"""Test the first agent request without a live model or network connection."""

import asyncio
from unittest.mock import AsyncMock

from mcp import Client
from mcp.types import CallToolResult

from mcp_lab.agent import run_first_request


def test_first_request_reads_employee_record_once() -> None:
    employee = {
        "employee_id": "EMP-001",
        "role": "manager",
        "tenure_years": 2,
        "current_equipment": None,
    }
    client = AsyncMock(spec=Client)
    client.call_tool.return_value = CallToolResult(
        content=[], structured_content=employee
    )

    result = asyncio.run(run_first_request(client))

    client.call_tool.assert_awaited_once_with(
        "get_employee_info", {"employee_id": "EMP-001"}
    )
    assert result == employee
