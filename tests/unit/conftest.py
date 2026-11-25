"""Shared fixtures for equipment request unit tests."""

from datetime import date

import pytest

from mcp_lab.server.models import Employee


@pytest.fixture
def employee() -> Employee:
    """A standard employee with confirmed empty equipment history."""
    return Employee(role="standard", start_date=date(2020, 1, 1), equipment_history=())
