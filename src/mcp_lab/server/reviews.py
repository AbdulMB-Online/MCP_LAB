"""Persist equipment review requests and return their recorded details."""

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from mcp_lab.server.models import ReviewReceipt


def flag_for_human_review(
    employee_id: str,
    request: str,
    reason: str,
    *,
    db_path: Path,
    request_id: str,
) -> ReviewReceipt:
    """Record an escalation, reusing the receipt on an identical request ID retry."""
    if not employee_id.strip() or not request.strip() or not reason.strip():
        raise ValueError("Employee ID, request, and reason must be nonempty.")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("A nonempty request ID is required.")

    uri = f"{db_path.resolve().as_uri()}?mode=rw"
    with closing(sqlite3.connect(uri, uri=True)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO equipment_requests (request_id, employee_id, request_text)
            VALUES (?, ?, ?)
            ON CONFLICT (request_id) DO NOTHING
            """,
            (request_id, employee_id, request),
        )
        stored = connection.execute(
            "SELECT employee_id, request_text FROM equipment_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if (stored["employee_id"], stored["request_text"]) != (employee_id, request):
            raise ValueError(f"Request {request_id!r} already has different details.")

        connection.execute(
            """
            INSERT INTO request_decisions (request_id, outcome, reason, review_id)
            VALUES (?, 'escalated', ?, ?)
            ON CONFLICT (request_id) DO NOTHING
            """,
            (request_id, reason, f"REV-{uuid4().hex}"),
        )
        decision = connection.execute(
            """
            SELECT outcome, reason, review_id, decided_at
            FROM request_decisions WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        if decision["outcome"] != "escalated" or decision["reason"] != reason:
            raise ValueError(
                f"Request {request_id!r} already has a different decision."
            )

        receipt: ReviewReceipt = {
            "outcome": "escalated",
            "request_id": request_id,
            "review_id": decision["review_id"],
            "reason": decision["reason"],
            "created_at": decision["decided_at"],
        }
    return receipt
