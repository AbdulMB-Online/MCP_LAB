"""Employee equipment business functions, independent of MCP transport."""

from collections.abc import Mapping
from datetime import date

from mcp_lab.server.models import CurrentEquipment, Employee, EmployeeInfo


class EmployeeNotFoundError(LookupError):
    """The requested employee ID is missing from the employee records."""


def get_employee_info(
    employee_id: str,
    *,
    employees: Mapping[str, Employee],
    as_of: date,
) -> EmployeeInfo:
    """Look up an employee and calculate completed years of employment."""
    try:
        employee = employees[employee_id]
    except KeyError as error:
        raise EmployeeNotFoundError(f"Unknown employee ID {employee_id!r}") from error
    start_date = employee.start_date
    if start_date > as_of:
        raise ValueError(
            f"Employee {employee_id!r} start date is after the lookup date"
        )

    anniversary_pending = (as_of.month, as_of.day) < (
        start_date.month,
        start_date.day,
    )
    tenure_years = as_of.year - start_date.year - int(anniversary_pending)
    current_equipment: list[CurrentEquipment] | None = None
    history = employee.equipment_history
    if history is not None:
        if any(asset.issued_on > as_of for asset in history):
            raise ValueError(
                f"Employee {employee_id!r} has an asset with a future issue date"
            )
        current_equipment = [
            {
                "asset_id": asset.asset_id,
                "item": asset.item,
                "issued_on": asset.issued_on.isoformat(),
                "status": "active",
            }
            for asset in history
            if asset.status == "active"
        ]

    return {
        "employee_id": employee_id,
        "role": employee.role,
        "tenure_years": tenure_years,
        "current_equipment": current_equipment,
    }
