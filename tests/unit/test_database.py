"""Verify SQLite initialization through the command entry point."""

import sys
from pathlib import Path

import pytest

from mcp_lab.server import database
from mcp_lab.server.repository import load_employee


def test_database_entry_point_seeds_requested_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "new" / "equipment.sqlite3"
    monkeypatch.setattr(sys, "argv", ["database", "--db", str(db_path), "--seed"])

    database.main()

    assert load_employee(db_path, "EMP-001") is not None


def test_database_cli_returns_failure_for_missing_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "database",
            "--db",
            str(tmp_path / "equipment.sqlite3"),
            "--sql-dir",
            str(tmp_path / "missing"),
        ],
    )

    with pytest.raises(SystemExit, match="^1$"):
        database.main()
