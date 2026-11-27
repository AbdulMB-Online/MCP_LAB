"""Serve equipment request tools over Streamable HTTP."""

import json
import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from mcp_lab.server import eligibility, equipment, policy, reviews
from mcp_lab.server.models import (
    EligibilityDecision,
    EmployeeInfo,
    ItemPolicyLimits,
    ReviewReceipt,
)
from mcp_lab.server.repository import load_employee, load_policies


def _lookup_result(record: dict) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(record))],
        structured_content=record,
    )


def _lookup_error(code: str, message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        is_error=True,
        meta={"equipment_error": code},
    )


def _today() -> date:
    return datetime.now(UTC).date()


class EquipmentTools:
    def __init__(self, db_path: Path, today: Callable[[], date] = _today) -> None:
        self.db_path = db_path
        self.today = today

    def get_employee_info(
        self, employee_id: str
    ) -> Annotated[CallToolResult, EmployeeInfo]:
        """Look up an employee's role, completed years of tenure, and active equipment."""
        try:
            employee = load_employee(self.db_path, employee_id)
            employees = {employee_id: employee} if employee is not None else {}
            record = equipment.get_employee_info(
                employee_id,
                employees=employees,
                as_of=self.today(),
            )
        except equipment.EmployeeNotFoundError as error:
            return _lookup_error("employee_not_found", str(error))
        except ValueError:
            return _lookup_error(
                "invalid_employee_dates", "Employee record contains an invalid date."
            )
        return _lookup_result(record)

    def get_policy_limits(
        self,
        role: str,
    ) -> Annotated[CallToolResult, dict[str, ItemPolicyLimits]]:
        """Look up allowed equipment limits and explicit prohibitions for a role."""
        try:
            limits = policy.get_policy_limits(
                role, policies=load_policies(self.db_path, role)
            )
        except policy.PolicyNotFoundError as error:
            return _lookup_error("policy_not_found", str(error))
        return _lookup_result(limits)

    def check_request_eligibility(
        self,
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
            employee = load_employee(self.db_path, employee_id)
        except ValueError:
            return {
                "outcome": "escalated",
                "reason": "The employee record contains an invalid date.",
            }
        policies = (
            load_policies(self.db_path, employee.role) if employee is not None else ()
        )
        return eligibility.check_request_eligibility(
            item,
            employee=employee,
            policies=policies,
            request_kind=request_kind,
            replacement_asset_id=replacement_asset_id,
            as_of=self.today(),
        )

    def flag_for_human_review(
        self,
        employee_id: str,
        request: str,
        reason: str,
        request_id: str,
    ) -> ReviewReceipt:
        """Record a human review using the ID assigned at intake. Reuse it for retries."""
        try:
            return reviews.flag_for_human_review(
                employee_id,
                request,
                reason,
                db_path=self.db_path,
                request_id=request_id,
            )
        except ValueError as error:
            raise ToolError(str(error)) from error


def create_server(tools: EquipmentTools) -> MCPServer:
    server = MCPServer("IT Equipment Requests")
    server.add_tool(tools.get_employee_info)
    server.add_tool(tools.get_policy_limits)
    server.add_tool(tools.check_request_eligibility)
    server.add_tool(tools.flag_for_human_review)
    return server


server = create_server(
    EquipmentTools(Path(os.environ.get("MCP_DB_PATH", "tmp/equipment.sqlite3")))
)


def main() -> None:
    server.run(
        transport="streamable-http",
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8001")),
    )


if __name__ == "__main__":
    main()
