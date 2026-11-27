"""Test employee lookup directly."""

from dataclasses import replace
from datetime import date
from functools import partial

import pytest

from mcp_lab.server.equipment import EmployeeNotFoundError, get_employee_info
from mcp_lab.server.models import Employee, EquipmentAsset

AS_OF = date(2026, 11, 24)
_lookup = partial(get_employee_info, as_of=AS_OF)


class TestEmployeeLookup:
    def test_returns_the_requested_employee(self, employee: Employee) -> None:
        laptop = EquipmentAsset("LAP-002", "laptop", date(2024, 1, 15), "active")
        employees = {
            "EMP-001": replace(employee, role="manager"),
            "EMP-002": replace(
                employee,
                start_date=date(2022, 11, 25),
                equipment_history=(laptop,),
            ),
        }

        assert _lookup("EMP-002", employees=employees) == {
            "employee_id": "EMP-002",
            "role": "standard",
            "tenure_years": 3,
            "current_equipment": [
                {
                    "asset_id": "LAP-002",
                    "item": "laptop",
                    "issued_on": "2024-01-15",
                    "status": "active",
                }
            ],
        }

    def test_excludes_retired_equipment(self, employee: Employee) -> None:
        record = replace(
            employee,
            equipment_history=(
                EquipmentAsset("MON-001", "monitor", date(2025, 4, 1), "active"),
                EquipmentAsset("LAP-OLD", "laptop", date(2019, 6, 1), "retired"),
                EquipmentAsset("LAP-CURRENT", "laptop", date(2024, 1, 15), "active"),
            ),
        )

        result = _lookup("EMP-001", employees={"EMP-001": record})

        assets = result["current_equipment"]
        assert [asset["asset_id"] for asset in assets] == ["MON-001", "LAP-CURRENT"]


class TestEmployeeEdgeCases:
    def test_empty_history_returns_empty_list(self, employee: Employee) -> None:
        result = _lookup("EMP-001", employees={"EMP-001": employee})

        assert result["current_equipment"] == []

    def test_unavailable_history_returns_none(self, employee: Employee) -> None:
        record = replace(employee, equipment_history=None)

        result = _lookup("EMP-001", employees={"EMP-001": record})

        assert result["current_equipment"] is None

    def test_rejects_unknown_id(self, employee: Employee) -> None:
        with pytest.raises(EmployeeNotFoundError, match="EMP-999"):
            _lookup("EMP-999", employees={"EMP-001": employee})

    def test_rejects_future_start_date(self, employee: Employee) -> None:
        record = replace(employee, start_date=date(2026, 11, 25))

        with pytest.raises(ValueError, match="start date"):
            _lookup("EMP-001", employees={"EMP-001": record})

    def test_rejects_future_issue_date(self, employee: Employee) -> None:
        laptop = EquipmentAsset("LAP-FUT", "laptop", date(2026, 11, 25), "active")
        record = replace(employee, equipment_history=(laptop,))

        with pytest.raises(ValueError, match="future issue date"):
            _lookup("EMP-001", employees={"EMP-001": record})

    def test_counts_completed_year_on_exact_anniversary(
        self, employee: Employee
    ) -> None:
        record = replace(employee, start_date=date(2022, 11, 24))

        result = _lookup("EMP-001", employees={"EMP-001": record})

        assert result["tenure_years"] == 4
