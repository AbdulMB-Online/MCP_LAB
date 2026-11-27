"""Test review recording directly with a temporary SQLite database."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, suppress
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
    def test_reuses_receipt(self, submit: SubmitReview) -> None:
        original = submit()

        assert submit() == original

    def test_rejects_changed_employee(self, submit: SubmitReview) -> None:
        submit()

        with pytest.raises(ValueError, match="REQ-001.*different details"):
            submit(employee_id="EMP-002")

    def test_rejects_changed_reason(self, submit: SubmitReview) -> None:
        submit()

        with pytest.raises(ValueError, match="REQ-001.*different decision"):
            submit(reason="Missing history")

    def test_rejects_changed_request_text(self, submit: SubmitReview) -> None:
        submit()

        with pytest.raises(ValueError, match="REQ-001.*different details"):
            submit(request="Request another monitor")

    def test_preserves_original_request_after_conflicting_retry(
        self, db: sqlite3.Connection, submit: SubmitReview
    ) -> None:
        original = submit()

        with suppress(ValueError):
            submit(request="Request another monitor")

        assert db.execute(
            "SELECT review_id, request_text FROM human_reviews"
        ).fetchall() == [(original["review_id"], "Replace my laptop")]


@pytest.fixture
def broken_review_db(db: sqlite3.Connection) -> sqlite3.Connection:
    with db:
        db.execute("""
            CREATE TRIGGER reject_decision BEFORE INSERT ON request_decisions
            BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END
        """)
    return db


@pytest.mark.usefixtures("broken_review_db")
def test_review_write_failure_propagates(submit: SubmitReview) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="simulated write failure"):
        submit()


def test_rolls_back_failed_write(
    broken_review_db: sqlite3.Connection, submit: SubmitReview
) -> None:
    with suppress(sqlite3.IntegrityError):
        submit()

    assert broken_review_db.execute(
        "SELECT count(*) FROM equipment_requests"
    ).fetchone() == (0,)


@pytest.mark.parametrize("field", ["employee_id", "request", "reason", "request_id"])
def test_rejects_blank_fields(submit: SubmitReview, field: str) -> None:
    with pytest.raises(ValueError, match="nonempty"):
        submit(**{field: "   "})


def test_missing_request_id_cannot_create_review(review_db: Path) -> None:
    with pytest.raises(TypeError, match="request_id"):
        flag_for_human_review("EMP-001", "Laptop", "Missing history", db_path=review_db)


def test_retry_after_lost_receipt_keeps_one_review(
    review_db: Path, db: sqlite3.Connection
) -> None:
    request = prepare_review("EMP-001", "Laptop", "Missing history")
    flag_for_human_review(**request, db_path=review_db)
    flag_for_human_review(**request, db_path=review_db)

    assert db.execute("SELECT count(*) FROM human_reviews").fetchone() == (1,)


def test_review_receipt_reports_escalation(submit: SubmitReview) -> None:
    receipt = submit()
    assert receipt["outcome"] == "escalated"


def test_review_receipt_uses_utc(submit: SubmitReview) -> None:
    receipt = submit()
    assert datetime.fromisoformat(receipt["created_at"]).utcoffset() == timedelta(0)
