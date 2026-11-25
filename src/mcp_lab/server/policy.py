"""Equipment policy lookup rules."""

from collections.abc import Iterable

from mcp_lab.server.models import EquipmentPolicy, ItemPolicyLimits


class PolicyNotFoundError(LookupError):
    """No equipment policy is configured for the requested role."""


def get_policy_limits(
    role: str, *, policies: Iterable[EquipmentPolicy]
) -> dict[str, ItemPolicyLimits]:
    """Return the equipment rules for the requested role, keyed by item."""
    limits: dict[str, ItemPolicyLimits] = {
        policy.item: {
            "disposition": policy.disposition,
            "max_active_quantity": policy.max_active_quantity,
            "replacement_interval_years": policy.replacement_interval_years,
        }
        for policy in policies
        if policy.role == role
    }
    if not limits:
        raise PolicyNotFoundError(f"No equipment policy for role {role!r}")
    return limits
