"""Expected outcomes and synthetic records for the ReAct evaluation."""

from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from sqlite3 import connect

from mcp_lab.server.database import initialize_database

FIRST_LAPTOP = "Can I get my first work laptop?"
FIRST_MONITOR = "Can I get my first work monitor?"
SECOND_MONITOR = "I need a second monitor."
CONSOLE = "I need a gaming console for work."
REPLACE_LAPTOP = "Replace my damaged laptop LAP-002."
CLARIFY_REQUEST = "does not identify one supported item and intent"


@dataclass(frozen=True)
class Case:
    name: str
    employee: str
    request: str
    outcome: str
    reason: str | tuple[str, ...]
    role: str = "standard"
    item: str = "laptop"
    intent: str = "first_issue"
    asset: str | None = None
    as_of: date = date(2026, 11, 26)
    stop_after: str = "check_request_eligibility"
    repeat: bool = False
    role_override: str | None = None


CASES = (
    # First issues and explicit prohibitions.
    Case(
        "first-laptop", "EMP-001", FIRST_LAPTOP, "approved", "First issue", repeat=True
    ),
    Case(
        "first-monitor-with-laptop",
        "EMP-002",
        FIRST_MONITOR,
        "approved",
        "First issue",
        item="monitor",
    ),
    Case(
        "first-laptop-already-owned",
        "EMP-002",
        FIRST_LAPTOP,
        "escalated",
        "Prior equipment exists",
    ),
    Case(
        "first-laptop-retired-on-file",
        "EMP-RETIRED",
        FIRST_LAPTOP,
        "escalated",
        "Prior equipment exists",
    ),
    Case(
        "console-standard",
        "EMP-001",
        CONSOLE,
        "denied",
        "explicitly prohibits",
        item="gaming_console",
        intent="unknown",
        repeat=True,
    ),
    Case(
        "console-manager",
        "EMP-004",
        CONSOLE,
        "denied",
        "explicitly prohibits",
        role="manager",
        item="gaming_console",
        intent="unknown",
    ),
    # Additional equipment and active allocation.
    Case(
        "manager-second-monitor",
        "EMP-004",
        SECOND_MONITOR,
        "approved",
        "within the role's equipment limit",
        role="manager",
        item="monitor",
        intent="additional",
    ),
    Case(
        "standard-second-monitor",
        "EMP-004",
        SECOND_MONITOR,
        "escalated",
        "exceed the active equipment limit",
        item="monitor",
        intent="additional",
        role_override="standard",
    ),
    Case(
        "manager-at-monitor-limit",
        "EMP-FULL",
        "I need another monitor.",
        "escalated",
        "exceed the active equipment limit",
        role="manager",
        item="monitor",
        intent="additional",
    ),
    Case(
        "second-monitor-none-on-file",
        "EMP-001",
        SECOND_MONITOR,
        "escalated",
        "No active monitor",
        item="monitor",
        intent="additional",
    ),
    # Replacement boundaries and asset identity.
    Case(
        "replacement-day-before",
        "EMP-002",
        REPLACE_LAPTOP,
        "escalated",
        "not due for replacement until 2028-01-15",
        intent="replacement",
        asset="LAP-002",
        as_of=date(2028, 1, 14),
        repeat=True,
    ),
    Case(
        "replacement-on-due-date",
        "EMP-002",
        REPLACE_LAPTOP,
        "approved",
        "requires returning or retiring LAP-002",
        intent="replacement",
        asset="LAP-002",
        as_of=date(2028, 1, 15),
        repeat=True,
    ),
    Case(
        "leap-day-replacement",
        "EMP-LEAP",
        "Replace my laptop LAP-LEAP.",
        "approved",
        "requires returning or retiring LAP-LEAP",
        role="manager",
        intent="replacement",
        asset="LAP-LEAP",
        as_of=date(2026, 2, 28),
        repeat=True,
    ),
    Case(
        "unknown-asset",
        "EMP-002",
        "Replace my laptop LAP-999.",
        "escalated",
        "Identify the active laptop",
        intent="replacement",
        asset="LAP-999",
    ),
    Case(
        "asset-is-wrong-item-type",
        "EMP-004",
        "Replace my laptop MON-004.",
        "escalated",
        "Identify the active laptop",
        role="manager",
        intent="replacement",
        asset="MON-004",
    ),
    Case(
        "asset-is-retired",
        "EMP-004",
        "Replace my monitor MON-004-OLD.",
        "escalated",
        "Identify the active monitor",
        role="manager",
        item="monitor",
        intent="replacement",
        asset="MON-004-OLD",
    ),
    # Data quality and decision precedence.
    Case(
        "unknown-employee",
        "EMP-999",
        FIRST_LAPTOP,
        "escalated",
        "could not be verified",
        stop_after="get_employee_info",
    ),
    Case(
        "role-without-policy",
        "EMP-INTERN",
        FIRST_LAPTOP,
        "escalated",
        ("No policy covers", "No equipment policy is configured"),
        role="intern",
        stop_after="get_policy_limits",
    ),
    Case(
        "unavailable-history",
        "EMP-003",
        FIRST_LAPTOP,
        "escalated",
        "Equipment history is unavailable",
        role="manager",
        repeat=True,
    ),
    Case(
        "future-start-date",
        "EMP-FUTURE",
        FIRST_LAPTOP,
        "escalated",
        ("invalid date", "future start date"),
        stop_after="get_employee_info",
    ),
    Case(
        "future-asset-in-partial-history",
        "EMP-PARTIAL",
        CONSOLE,
        "escalated",
        ("invalid date", "future issue date"),
        item="gaming_console",
        intent="unknown",
        stop_after="get_employee_info",
        repeat=True,
    ),
    # Request wording and ambiguities.
    Case(
        "first-laptop-paraphrase",
        "EMP-001",
        "I need my first laptop for work.",
        "approved",
        "First issue",
    ),
    Case(
        "unspecified-issuance-intent",
        "EMP-001",
        "I need a laptop.",
        "escalated",
        CLARIFY_REQUEST,
        stop_after="intake",
        repeat=True,
    ),
    Case(
        "missing-replacement-asset",
        "EMP-002",
        "Replace my damaged laptop.",
        "escalated",
        CLARIFY_REQUEST,
        stop_after="intake",
    ),
    Case(
        "multiple-items",
        "EMP-001",
        "I need a laptop and a monitor.",
        "escalated",
        CLARIFY_REQUEST,
        stop_after="intake",
        repeat=True,
    ),
    Case(
        "uncovered-item",
        "EMP-001",
        "I need a headset.",
        "escalated",
        CLARIFY_REQUEST,
        stop_after="intake",
    ),
)


def seed_evaluation_database(path: Path, case: Case) -> None:
    initialize_database(path, seed=True)
    with closing(connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        db.executemany(
            "INSERT INTO employees VALUES (?, ?, ?, ?)",
            [
                ("EMP-RETIRED", "standard", "2020-01-01", "complete"),
                ("EMP-FULL", "manager", "2020-01-01", "complete"),
                ("EMP-LEAP", "manager", "2020-01-01", "complete"),
                ("EMP-INTERN", "intern", "2020-01-01", "complete"),
                ("EMP-FUTURE", "standard", "2099-01-01", "complete"),
                ("EMP-PARTIAL", "standard", "2020-01-01", "unknown"),
            ],
        )
        db.executemany(
            "INSERT INTO equipment_assets VALUES (?, ?, ?, ?, ?)",
            [
                ("LAP-RET", "EMP-RETIRED", "laptop", "2020-01-01", "retired"),
                ("MON-FULL-A", "EMP-FULL", "monitor", "2025-04-01", "active"),
                ("MON-FULL-B", "EMP-FULL", "monitor", "2025-04-01", "active"),
                ("LAP-LEAP", "EMP-LEAP", "laptop", "2024-02-29", "active"),
                ("MON-FUTURE", "EMP-PARTIAL", "monitor", "2099-01-01", "active"),
            ],
        )
        if case.role_override is not None:
            db.execute(
                "UPDATE employees SET role = ? WHERE employee_id = ?",
                (case.role_override, case.employee),
            )
