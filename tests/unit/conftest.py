"""Shared fixtures for equipment request unit tests."""

from datetime import date
from unittest.mock import MagicMock

import pytest
from mcp import Client

from mcp_lab.server.models import Employee


@pytest.fixture
def employee() -> Employee:
    """A standard employee with confirmed empty equipment history."""
    return Employee(role="standard", start_date=date(2020, 1, 1), equipment_history=())


@pytest.fixture
def cli_client() -> MagicMock:
    """Replace MCP connection setup while preserving the client call contract."""
    client = MagicMock(spec=Client)
    client.__aenter__.return_value = client
    return client
