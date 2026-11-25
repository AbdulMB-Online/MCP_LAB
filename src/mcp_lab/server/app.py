"""Serve equipment request tools over Streamable HTTP."""

import os
from datetime import UTC, datetime
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_lab.server import eligibility, equipment, policy, reviews
from mcp_lab.server.models import (
    EligibilityDecision,
    EmployeeInfo,
    ItemPolicyLimits,
    ReviewReceipt,
)
from mcp_lab.server.repository import load_employee, load_policies

_DB_PATH = Path(os.environ.get("MCP_DB_PATH", "tmp/equipment.sqlite3"))

server = MCPServer("IT Equipment Requests")


@server.tool()
def get_employee_info(employee_id: str) -> EmployeeInfo:
    """Look up an employee's role, completed years of tenure, and active equipment."""
    try:
        employee = load_employee(_DB_PATH, employee_id)
        employees = {employee_id: employee} if employee is not None else {}
        return equipment.get_employee_info(
            employee_id,
            employees=employees,
            as_of=datetime.now(UTC).date(),
        )
    except equipment.EmployeeNotFoundError as error:
        raise ToolError(str(error)) from error
    except ValueError as error:
        raise ToolError("Employee record contains an invalid date.") from error


@server.tool()
def get_policy_limits(role: str) -> dict[str, ItemPolicyLimits]:
    """Look up allowed equipment limits and explicit prohibitions for a role."""
    try:
        return policy.get_policy_limits(role, policies=load_policies(_DB_PATH, role))
    except policy.PolicyNotFoundError as error:
        raise ToolError(str(error)) from error


@server.tool()
def check_request_eligibility(
    employee_id: str,
    item: str,
    request_kind: str = "unknown",
    replacement_asset_id: str | None = None,
) -> EligibilityDecision:
    """Evaluate a request using first_issue, additional, or replacement intent.

    Use unknown for unclear intent. Specify the existing asset ID for a replacement.
    This tool evaluates the request without recording a decision or a review.
    """
    try:
        employee = load_employee(_DB_PATH, employee_id)
    except ValueError:
        return {
            "outcome": "escalated",
            "reason": "The employee record contains an invalid date.",
        }
    policies = load_policies(_DB_PATH, employee.role) if employee is not None else ()
    return eligibility.check_request_eligibility(
        item,
        employee=employee,
        policies=policies,
        request_kind=request_kind,
        replacement_asset_id=replacement_asset_id,
        as_of=datetime.now(UTC).date(),
    )


@server.tool()
def flag_for_human_review(
    employee_id: str,
    request: str,
    reason: str,
    request_id: str | None = None,
) -> ReviewReceipt:
    """Record a human review. Reuse the request ID when retrying a call."""
    try:
        return reviews.flag_for_human_review(
            employee_id, request, reason, db_path=_DB_PATH, request_id=request_id
        )
    except ValueError as error:
        raise ToolError(str(error)) from error


if __name__ == "__main__":
    server.run(
        transport="streamable-http",
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8001")),
    )
