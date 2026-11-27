"""Verify employee lookups against the SQLite schema and synthetic records."""

from datetime import date
from pathlib import Path

import pytest

from mcp_lab.server.database import initialize_database
from mcp_lab.server.models import Employee, EquipmentAsset, EquipmentPolicy
from mcp_lab.server.repository import load_employee, load_policies


@pytest.fixture
def seeded_database(tmp_path: Path) -> Path:
    path = tmp_path / "equipment.sqlite3"
    initialize_database(path, seed=True)
    return path


@pytest.mark.parametrize(
    ("employee_id", "expected"),
    [
        ("EMP-001", Employee("standard", date(2020, 1, 1), ())),
        ("EMP-003", Employee("manager", date(2021, 3, 15), None)),
        (
            "EMP-002",
            Employee(
                "standard",
                date(2022, 11, 25),
                (EquipmentAsset("LAP-002", "laptop", date(2024, 1, 15), "active"),),
            ),
        ),
        ("EMP-999", None),
    ],
    ids=["confirmed-empty", "history-unknown", "asset-on-file", "employee-missing"],
)
def test_employee_mapping(
    seeded_database: Path,
    employee_id: str,
    expected: Employee | None,
) -> None:
    # Act
    employee = load_employee(seeded_database, employee_id)

    # Assert
    assert employee == expected


def test_maps_policies_for_requested_role(seeded_database: Path) -> None:
    policies = load_policies(seeded_database, "manager")

    assert policies == (
        EquipmentPolicy("manager", "gaming_console", "prohibited", None, None),
        EquipmentPolicy("manager", "laptop", "allowed", 1, 2),
        EquipmentPolicy("manager", "monitor", "allowed", 2, 3),
    )
