"""Check that the paired eval isolates the changed draft and scores its verdict."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from evals.reflection_eval import build_cases, evaluate, summarize


@pytest.fixture
def baseline_rows() -> list[dict]:
    records = [
        ("first-laptop", "approved", "Approved. First issue is allowed."),
        ("console-standard", "denied", "Denied. Role policy prohibits this item."),
        (
            "replacement-day-before",
            "escalated",
            "Filed for review. LAP-002 is due on 2028-01-15.",
        ),
        (
            "unavailable-history",
            "escalated",
            "Filed for review. History is unavailable.",
        ),
    ]
    return [
        {
            "case": name,
            "attempt": 1,
            "employee_id": "EMP-TEST",
            "request": "Synthetic request",
            "checks": {
                "outcome": True,
                "reason": True,
                "tool_evidence": True,
                "review_persistence": True,
            },
            "result": {
                "draft": draft,
                "decision": {"outcome": outcome, "reason": "Synthetic reason"},
                "trace": [],
                **(
                    {"review": {"review_id": f"REV-{name}"}}
                    if outcome == "escalated"
                    else {}
                ),
            },
        }
        for name, outcome, draft in records
    ]


def test_flawed_draft_keeps_the_same_replacement_evidence(
    baseline_rows: list[dict],
) -> None:
    cases = build_cases(baseline_rows)
    supported, flawed = [
        case for case in cases if case.name == "replacement-day-before"
    ]
    supported_result, flawed_result = (
        deepcopy(supported.result),
        deepcopy(flawed.result),
    )

    supported_draft = supported_result.pop("draft")
    flawed_draft = flawed_result.pop("draft")

    assert (flawed_result, flawed_draft, flawed.expected_verdict) == (
        supported_result,
        supported_draft.replace("2028-01-15", "2028-01-16"),
        "flagged",
    )


def test_requires_a_passing_baseline_with_all_four_cases(
    baseline_rows: list[dict],
) -> None:
    baseline_rows[-1]["checks"]["review_persistence"] = False

    with pytest.raises(ValueError, match="unavailable-history"):
        build_cases(baseline_rows)


def test_flawed_receipt_changes_only_one_character_in_the_draft(
    baseline_rows: list[dict],
) -> None:
    supported, flawed = [
        case
        for case in build_cases(baseline_rows)
        if case.name == "unavailable-history"
    ]
    review_id = supported.result["review"]["review_id"]

    assert (flawed.result["review"], flawed.result["draft"]) == (
        supported.result["review"],
        f"{supported.result['draft']} Your review ID is {review_id[:-1]}1.",
    )


def test_score_separates_missed_errors_from_correct_drafts_withheld() -> None:
    rows = [
        {"expected_verdict": "confirmed", "verdict": "confirmed", "elapsed_ms": 10},
        {"expected_verdict": "confirmed", "verdict": "flagged", "elapsed_ms": 20},
        {"expected_verdict": "flagged", "verdict": "confirmed", "elapsed_ms": 30},
        {"expected_verdict": "flagged", "verdict": "flagged", "elapsed_ms": 40},
    ]
    result = summarize(rows)

    assert result == {
        "cases": 4,
        "passed": 2,
        "without_reflection": {"correct_released": 2, "flawed_released": 2},
        "with_reflection": {
            "correct_released": 1,
            "correct_withheld": 1,
            "flawed_released": 1,
            "flawed_flagged": 1,
            "unverified": 0,
        },
        "median_review_ms": 25,
    }


def test_replays_all_eight_drafts_through_the_reviewer(
    baseline_rows: list[dict],
) -> None:
    reviewer = AsyncMock()
    reviewer.review.side_effect = [
        {"verdict": verdict, "reason": "Scripted verdict."}
        for _ in baseline_rows
        for verdict in ("confirmed", "flagged")
    ]

    rows = asyncio.run(evaluate(build_cases(baseline_rows), reviewer))

    assert (len(rows), summarize(rows)["passed"], reviewer.review.await_count) == (
        8,
        8,
        8,
    )
