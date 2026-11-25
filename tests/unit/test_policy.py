"""Test equipment policy lookup directly."""

import pytest

from mcp_lab.server.models import EquipmentPolicy
from mcp_lab.server.policy import PolicyNotFoundError, get_policy_limits

POLICIES = (
    EquipmentPolicy("standard", "laptop", "allowed", 1, 4),
    EquipmentPolicy("manager", "laptop", "allowed", 1, 2),
)


@pytest.mark.parametrize(("role", "years"), [("standard", 4), ("manager", 2)])
def test_laptop_limits_by_role(role: str, years: int) -> None:
    assert get_policy_limits(role, policies=POLICIES) == {
        "laptop": {
            "disposition": "allowed",
            "max_active_quantity": 1,
            "replacement_interval_years": years,
        },
    }


def test_rejects_unknown_role() -> None:
    with pytest.raises(PolicyNotFoundError, match="contractor"):
        get_policy_limits("contractor", policies=POLICIES)
