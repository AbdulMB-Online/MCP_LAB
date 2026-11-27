"""Read employees, equipment, and policy records from SQLite."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from mcp_lab.server.models import Employee, EquipmentAsset, EquipmentPolicy


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_employee(db_path: Path, employee_id: str) -> Employee | None:
    """Load one employee with their equipment history, if available."""
    with closing(_connect_read_only(db_path)) as connection:
        # Read the history status and its assets from the same database snapshot.
        connection.execute("BEGIN")
        employee = connection.execute(
            """
            SELECT role, start_date, equipment_history_status
            FROM employees
            WHERE employee_id = ?
            """,
            (employee_id,),
        ).fetchone()
        if employee is None:
            return None

        assets = connection.execute(
            """
            SELECT asset_id, item, issued_on, status
            FROM equipment_assets
            WHERE employee_id = ?
            ORDER BY issued_on, asset_id
            """,
            (employee_id,),
        )
        known_history = tuple(
            EquipmentAsset(
                asset_id=asset["asset_id"],
                item=asset["item"],
                issued_on=date.fromisoformat(asset["issued_on"]),
                status=asset["status"],
            )
            for asset in assets
        )

        complete = employee["equipment_history_status"] == "complete"
        return Employee(
            role=employee["role"],
            start_date=date.fromisoformat(employee["start_date"]),
            equipment_history=known_history if complete else None,
            partial_history=() if complete else known_history,
        )


def load_policies(db_path: Path, role: str) -> tuple[EquipmentPolicy, ...]:
    """Load the configured equipment policies for one role."""
    with closing(_connect_read_only(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT role, item, disposition, max_active_quantity,
                   replacement_interval_years
            FROM equipment_policies
            WHERE role = ?
            ORDER BY item
            """,
            (role,),
        )
        return tuple(
            EquipmentPolicy(
                role=row["role"],
                item=row["item"],
                disposition=row["disposition"],
                max_active_quantity=row["max_active_quantity"],
                replacement_interval_years=row["replacement_interval_years"],
            )
            for row in rows
        )
