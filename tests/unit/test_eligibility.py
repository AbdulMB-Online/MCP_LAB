"""Test equipment eligibility decisions directly."""

import re
from dataclasses import replace
from datetime import date
from functools import partial

import pytest

from mcp_lab.server.eligibility import check_request_eligibility
from mcp_lab.server.models import (
    EligibilityDecision,
    Employee,
    EquipmentAsset,
    EquipmentPolicy,
)

AS_OF = date(2026, 11, 24)
POLICIES = (
    EquipmentPolicy("standard", "laptop", "allowed", 1, 4),
    EquipmentPolicy("standard", "monitor", "allowed", 1, 3),
    EquipmentPolicy("standard", "gaming_console", "prohibited", None, None),
    EquipmentPolicy("manager", "laptop", "allowed", 1, 2),
    EquipmentPolicy("manager", "monitor", "allowed", 2, 3),
)
DUE_LAPTOP = EquipmentAsset("LAP-100", "laptop", date(2022, 11, 24), "active")
LEAP_DAY_EMPLOYEE = Employee(
    role="manager",
    start_date=date(2020, 1, 1),
    equipment_history=(
        EquipmentAsset("LAP-100", "laptop", date(2024, 2, 29), "active"),
    ),
)
_check = partial(
    check_request_eligibility,
    item="laptop",
    request_kind="first_issue",
    policies=POLICIES,
    as_of=AS_OF,
)


def _assert_review(decision: EligibilityDecision, reason: str) -> None:
    assert (decision["outcome"], reason in decision["reason"]) == ("escalated", True)


class TestIssuance:
    def test_approves_first_laptop(self, employee: Employee) -> None:
        assert _check(employee=employee)["outcome"] == "approved"

    def test_denies_prohibited_gaming_console(self, employee: Employee) -> None:
        assert _check(employee=employee, item="gaming_console")["outcome"] == "denied"

    def test_approves_second_monitor_before_refresh(self, employee: Employee) -> None:
        manager = replace(
            employee,
            role="manager",
            equipment_history=(
                EquipmentAsset("MON-100", "monitor", date(2026, 11, 1), "active"),
                EquipmentAsset("MON-099", "monitor", date(2025, 4, 1), "retired"),
            ),
        )

        decision = _check(employee=manager, item="monitor", request_kind="additional")
        assert decision["outcome"] == "approved"

    def test_reviews_additional_monitor_at_limit(self, employee: Employee) -> None:
        monitor = EquipmentAsset("MON-100", "monitor", date(2025, 4, 1), "active")
        record = replace(employee, equipment_history=(monitor,))

        decision = _check(employee=record, item="monitor", request_kind="additional")
        _assert_review(decision, "exceed the active equipment limit")

    def test_reviews_additional_laptop_with_none_on_file(
        self, employee: Employee
    ) -> None:
        decision = _check(employee=employee, request_kind="additional")

        _assert_review(decision, "No active laptop")

    def test_reviews_first_issue_with_mixed_case_asset(
        self, employee: Employee
    ) -> None:
        record = replace(
            employee, equipment_history=(replace(DUE_LAPTOP, item="Laptop"),)
        )

        _assert_review(_check(employee=record), "Prior equipment exists")

    def test_reviews_first_issue_with_retired_laptop(self, employee: Employee) -> None:
        retired = EquipmentAsset("LAP-OLD", "laptop", date(2020, 1, 15), "retired")
        record = replace(employee, equipment_history=(retired,))

        _assert_review(_check(employee=record), "Prior equipment exists")


class TestReplacement:
    def test_approves_replacement_on_due_date(self, employee: Employee) -> None:
        record = replace(employee, equipment_history=(DUE_LAPTOP,))

        decision = _check(
            employee=record, request_kind="replacement", replacement_asset_id="LAP-100"
        )
        assert decision["outcome"] == "approved"

    def test_approval_explains_what_to_do_with_old_asset(
        self, employee: Employee
    ) -> None:
        record = replace(employee, equipment_history=(DUE_LAPTOP,))

        decision = _check(
            employee=record, request_kind="replacement", replacement_asset_id="LAP-100"
        )

        obligation = r"\b(?:must|requir\w*)\s+(?:be\s+)?return\w*\s+or\s+retir\w*\b"
        reason = decision["reason"].lower()
        assert (
            decision["outcome"],
            "lap-100" in reason,
            bool(re.search(obligation, reason)),
        ) == ("approved", True, True)

    @pytest.mark.parametrize(
        ("day", "outcome", "reason"),
        [
            pytest.param(27, "escalated", "2026-02-28", id="day-before"),
            pytest.param(28, "approved", "LAP-100", id="due-date"),
        ],
    )
    def test_leap_day_due_date(self, day: int, outcome: str, reason: str) -> None:
        decision = _check(
            employee=LEAP_DAY_EMPLOYEE,
            request_kind="replacement",
            replacement_asset_id="LAP-100",
            as_of=date(2026, 2, day),
        )

        assert (decision["outcome"], reason in decision["reason"]) == (outcome, True)

    def test_reviews_unknown_asset(self) -> None:
        decision = _check(
            employee=LEAP_DAY_EMPLOYEE,
            request_kind="replacement",
            replacement_asset_id="LAP-999",
            as_of=date(2026, 2, 28),
        )

        _assert_review(decision, "active laptop")

    def test_reviews_replacement_above_active_limit(self, employee: Employee) -> None:
        extra = EquipmentAsset("LAP-101", "laptop", date(2024, 6, 1), "active")
        record = replace(employee, equipment_history=(DUE_LAPTOP, extra))

        decision = _check(
            employee=record, request_kind="replacement", replacement_asset_id="LAP-100"
        )
        _assert_review(decision, "already exceeds the limit")


class TestReviewReasons:
    def test_missing_equipment_history(self, employee: Employee) -> None:
        record = replace(employee, equipment_history=None)
        _assert_review(_check(employee=record), "history is unavailable")

    def test_unknown_request_intent(self, employee: Employee) -> None:
        _assert_review(
            _check(employee=employee, request_kind="unknown"), "request intent"
        )

    def test_uncovered_item(self, employee: Employee) -> None:
        _assert_review(
            _check(employee=employee, item="headset"), "No equipment policy applies"
        )

    def test_unverified_employee(self) -> None:
        _assert_review(_check(employee=None), "could not be verified")

    def test_role_without_policy(self, employee: Employee) -> None:
        record = replace(employee, role="contractor")
        _assert_review(_check(employee=record), "No equipment policy is configured")

    def test_future_start_date(self, employee: Employee) -> None:
        record = replace(employee, start_date=date(2026, 11, 25))
        _assert_review(_check(employee=record), "future start date")

    def test_future_issue_date(self, employee: Employee) -> None:
        laptop = EquipmentAsset("LAP-FUT", "laptop", date(2026, 11, 25), "active")
        record = replace(employee, equipment_history=(laptop,))

        _assert_review(
            _check(employee=record, request_kind="additional"), "future issue date"
        )

    def test_additional_request_with_replacement_id(self, employee: Employee) -> None:
        decision = _check(
            employee=employee, request_kind="additional", replacement_asset_id="LAP-100"
        )
        _assert_review(decision, "supplied replacement asset")


@pytest.mark.parametrize("item", ["laptop", "gaming_console"])
def test_future_monitor_blocks_eligibility(employee: Employee, item: str) -> None:
    monitor = EquipmentAsset("MON-001", "monitor", date(2026, 11, 25), "active")
    record = replace(employee, equipment_history=(monitor,))

    _assert_review(_check(employee=record, item=item), "future issue date")


def test_invalid_replacement_interval_escalates(employee: Employee) -> None:
    policy = replace(POLICIES[0], replacement_interval_years=0)
    record = replace(employee, equipment_history=(DUE_LAPTOP,))
    decision = _check(
        employee=record,
        policies=(policy,),
        request_kind="replacement",
        replacement_asset_id="LAP-100",
    )
    _assert_review(decision, "replacement interval is invalid")


def test_replacement_beyond_supported_calendar_escalates(employee: Employee) -> None:
    policy = replace(POLICIES[0], replacement_interval_years=10_000)
    record = replace(employee, equipment_history=(DUE_LAPTOP,))
    decision = _check(
        employee=record,
        policies=(policy,),
        request_kind="replacement",
        replacement_asset_id="LAP-100",
    )
    _assert_review(decision, "outside the supported calendar")


def test_invalid_allocation_escalates(employee: Employee) -> None:
    policy = replace(POLICIES[0], max_active_quantity=0)
    decision = _check(employee=employee, policies=(policy,))
    _assert_review(decision, "allocation policy is invalid")
