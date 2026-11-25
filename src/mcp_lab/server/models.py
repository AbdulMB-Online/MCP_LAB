"""Equipment records and structured tool responses."""

from dataclasses import dataclass
from datetime import date
from typing import Literal, TypedDict


@dataclass(frozen=True)
class EquipmentAsset:
    """One active or retired asset in an employee's equipment history."""

    asset_id: str
    item: str
    issued_on: date
    status: Literal["active", "retired"]


@dataclass(frozen=True)
class Employee:
    """An employee record keyed by ID in the employee directory."""

    role: str
    start_date: date
    # None means the history is unknown. An empty tuple confirms no equipment.
    equipment_history: tuple[EquipmentAsset, ...] | None


@dataclass(frozen=True)
class EquipmentPolicy:
    """A policy record for one role and equipment item."""

    role: str
    item: str
    disposition: Literal["allowed", "prohibited"]
    max_active_quantity: int | None
    replacement_interval_years: int | None


class CurrentEquipment(TypedDict):
    """An active asset in an employee lookup response."""

    asset_id: str
    item: str
    issued_on: str
    status: Literal["active"]


class EmployeeInfo(TypedDict):
    """Employee details returned by the lookup tool."""

    employee_id: str
    role: str
    tenure_years: int
    current_equipment: list[CurrentEquipment] | None


class ItemPolicyLimits(TypedDict):
    """The policy details returned for one item."""

    disposition: Literal["allowed", "prohibited"]
    max_active_quantity: int | None
    replacement_interval_years: int | None


class EligibilityDecision(TypedDict):
    """An eligibility outcome with a reason for the employee or reviewer."""

    outcome: Literal["approved", "denied", "escalated"]
    reason: str


class ReviewReceipt(TypedDict):
    """The receipt for a committed human review."""

    outcome: Literal["escalated"]
    request_id: str
    review_id: str
    reason: str
    created_at: str
