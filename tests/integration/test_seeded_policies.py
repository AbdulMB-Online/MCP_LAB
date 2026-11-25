"""Check the seeded SQLite policy table against the documented rules."""

from pathlib import Path

import pytest

from mcp_lab.server.database import initialize_database
from mcp_lab.server.policy import get_policy_limits
from mcp_lab.server.repository import load_policies

ROLE_LIMITS = {"standard": (4, 1), "manager": (2, 2)}


@pytest.mark.parametrize("role", ROLE_LIMITS)
def test_seeded_policies_match_requirements(tmp_path: Path, role: str) -> None:
    db_path = tmp_path / "equipment.sqlite3"
    initialize_database(db_path, seed=True)
    laptop_years, monitor_limit = ROLE_LIMITS[role]

    assert get_policy_limits(role, policies=load_policies(db_path, role)) == {
        "laptop": {
            "disposition": "allowed",
            "max_active_quantity": 1,
            "replacement_interval_years": laptop_years,
        },
        "monitor": {
            "disposition": "allowed",
            "max_active_quantity": monitor_limit,
            "replacement_interval_years": 3,
        },
        "gaming_console": {
            "disposition": "prohibited",
            "max_active_quantity": None,
            "replacement_interval_years": None,
        },
    }
