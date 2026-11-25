"""Evaluate equipment requests against employee records and role policies."""

from calendar import monthrange
from collections.abc import Iterable
from datetime import date

from mcp_lab.server.models import (
    EligibilityDecision,
    Employee,
    EquipmentAsset,
    EquipmentPolicy,
)
from mcp_lab.server.policy import PolicyNotFoundError, get_policy_limits


def _review(reason: str) -> EligibilityDecision:
    return {"outcome": "escalated", "reason": reason}


def _normalize_item(item: str) -> str:
    return "_".join(item.casefold().replace("-", " ").split())


def _check_replacement(
    asset: EquipmentAsset, interval_years: int | None, as_of: date
) -> EligibilityDecision:
    if interval_years is None or interval_years <= 0:
        return _review("The replacement interval is invalid.")
    due_year = asset.issued_on.year + interval_years
    if due_year > date.max.year:
        return _review("The replacement date is outside the supported calendar.")

    month = asset.issued_on.month
    day = min(asset.issued_on.day, monthrange(due_year, month)[1])
    due_date = date(due_year, month, day)
    if as_of < due_date:
        return _review(f"{asset.asset_id} is not due for replacement until {due_date}.")
    return {
        "outcome": "approved",
        "reason": f"Replacement is due and requires returning or retiring {asset.asset_id}.",
    }


def check_request_eligibility(
    item: str,
    *,
    employee: Employee | None,
    policies: Iterable[EquipmentPolicy],
    request_kind: str,
    as_of: date,
    replacement_asset_id: str | None = None,
) -> EligibilityDecision:
    """Apply role prohibitions and rules for issuing and replacing equipment."""
    if employee is None:
        return _review("The employee could not be verified.")
    if employee.start_date > as_of:
        return _review("The employee record has a future start date.")

    item = _normalize_item(item)

    try:
        limits = get_policy_limits(employee.role, policies=policies).get(item)
    except PolicyNotFoundError:
        return _review(f"No equipment policy is configured for role {employee.role!r}.")

    if limits is None:
        return _review(f"No equipment policy applies to the requested item {item!r}.")
    if limits["disposition"] == "prohibited":
        return {
            "outcome": "denied",
            "reason": "The role policy explicitly prohibits this item.",
        }

    history = employee.equipment_history
    if history is None:
        return _review("Equipment history is unavailable.")

    maximum = limits["max_active_quantity"]
    if limits["disposition"] != "allowed" or maximum is None or maximum <= 0:
        return _review("The allocation policy is invalid.")

    item_history = [asset for asset in history if _normalize_item(asset.item) == item]
    if any(asset.issued_on > as_of for asset in item_history):
        return _review("The equipment history contains a future issue date.")
    if replacement_asset_id is not None and request_kind != "replacement":
        return _review("Clarify the request intent for the supplied replacement asset.")

    if request_kind == "first_issue":
        if item_history:
            return _review("Prior equipment exists. Clarify the request intent.")
        return {
            "outcome": "approved",
            "reason": "First issue is within the role's equipment limit.",
        }

    active_assets = [asset for asset in item_history if asset.status == "active"]
    if request_kind == "additional":
        if not active_assets:
            return _review(f"No active {item} is on file. Clarify the request intent.")
        if len(active_assets) >= maximum:
            return _review(
                "An additional item would exceed the active equipment limit."
            )
        return {
            "outcome": "approved",
            "reason": "An additional item is within the role's equipment limit.",
        }

    if request_kind == "replacement":
        replacement = next(
            (
                asset
                for asset in active_assets
                if asset.asset_id == replacement_asset_id
            ),
            None,
        )
        if replacement is None:
            return _review(f"Identify the active {item} to replace.")
        # Replacing one active asset leaves the active count unchanged.
        if len(active_assets) > maximum:
            return _review("The active equipment allocation already exceeds the limit.")
        return _check_replacement(
            replacement, limits["replacement_interval_years"], as_of
        )

    return _review("Clarify the request intent before evaluating eligibility.")
