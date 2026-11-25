"""Test review recording directly with a temporary SQLite database."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

import pytest

from mcp_lab.client import prepare_review
from mcp_lab.server.database import initialize_database
from mcp_lab.server.models import ReviewReceipt
from mcp_lab.server.reviews import flag_for_human_review

SubmitReview = partial[ReviewReceipt]


@pytest.fixture
def review_db(tmp_path: Path) -> Path:
    path = tmp_path / "equipment.sqlite3"
    initialize_database(path)
    return path


@pytest.fixture
def db(review_db: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(review_db)) as connection:
        yield connection


@pytest.fixture
def submit(review_db: Path) -> SubmitReview:
    """Submit a standard review, with overrides for the case under test."""
    return partial(
        flag_for_human_review,
        employee_id="EMP-001",
        request="Replace my laptop",
        reason="Early replacement",
        db_path=review_db,
        request_id="REQ-001",
    )


def test_persists_unverified_employee_review(
    db: sqlite3.Connection, submit: SubmitReview
) -> None:
    receipt = submit(
        employee_id="EMP-999",
        request="I need a laptop",
        reason="Employee not found",
    )

    assert receipt["outcome"] == "escalated"
    assert datetime.fromisoformat(receipt["created_at"]).utcoffset() == timedelta(0)
    rows = db.execute(
        "SELECT review_id, request_id, employee_id, request_text, reason, created_at "
        "FROM human_reviews"
    ).fetchall()
    assert rows == [
        (
            receipt["review_id"],
            receipt["request_id"],
            "EMP-999",
            "I need a laptop",
            "Employee not found",
            receipt["created_at"],
        )
    ]


class TestRetries:
    def test_reuses_receipt(self, db: sqlite3.Connection, submit: SubmitReview) -> None:
        original = submit()

        assert submit() == original
        assert db.execute("SELECT count(*) FROM human_reviews").fetchone() == (1,)

    def test_rejects_changed_employee(self, submit: SubmitReview) -> None:
        original = submit()

        with pytest.raises(ValueError, match="REQ-001.*different details"):
            submit(employee_id="EMP-002")

        assert submit() == original

    def test_rejects_changed_reason(
        self, db: sqlite3.Connection, submit: SubmitReview
    ) -> None:
        original = submit()

        with pytest.raises(ValueError, match="REQ-001.*different decision"):
            submit(reason="Missing history")

        assert submit() == original
        assert db.execute("SELECT count(*) FROM human_reviews").fetchone() == (1,)


def test_rolls_back_failed_write(db: sqlite3.Connection, submit: SubmitReview) -> None:
    with db:
        db.execute("""
            CREATE TRIGGER reject_decision BEFORE INSERT ON request_decisions
            BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END
        """)

    with pytest.raises(sqlite3.IntegrityError, match="simulated write failure"):
        submit()

    assert db.execute("SELECT count(*) FROM equipment_requests").fetchone() == (0,)


@pytest.mark.parametrize("field", ["employee_id", "request", "reason", "request_id"])
def test_rejects_blank_fields(
    db: sqlite3.Connection, submit: SubmitReview, field: str
) -> None:
    with pytest.raises(ValueError):
        submit(**{field: "   "})

    assert db.execute("SELECT count(*) FROM equipment_requests").fetchone() == (0,)


def test_missing_request_id_cannot_create_review(review_db: Path) -> None:
    with pytest.raises(TypeError, match="request_id"):
        flag_for_human_review("EMP-001", "Laptop", "Missing history", db_path=review_db)


def test_retry_after_lost_receipt_keeps_one_review(
    review_db: Path, db: sqlite3.Connection
) -> None:
    request = prepare_review("EMP-001", "Laptop", "Missing history")
    first = flag_for_human_review(**request, db_path=review_db)
    retry = flag_for_human_review(**request, db_path=review_db)
    assert first == retry
    assert db.execute("SELECT count(*) FROM human_reviews").fetchone() == (1,)
    other = prepare_review("EMP-001", "Laptop", "Missing history")
    assert other["request_id"] != request["request_id"]
