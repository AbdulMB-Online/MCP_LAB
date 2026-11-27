"""Verify the reviewer's independent context and verdict contract."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from mcp_lab.reviewer import ReviewerAgent

DRAFT = "Approved. Your laptop will arrive Friday."
APPROVAL = {
    "draft": DRAFT,
    "decision": {"outcome": "approved", "reason": "First issue is within the limit."},
    "trace": [
        {
            "tool": "check_request_eligibility",
            "arguments": {"employee_id": "EMP-001", "item": "laptop"},
            "observation": {
                "outcome": "approved",
                "reason": "First issue is within the limit.",
            },
            "rationale": "Internal equipment-agent planning context",
        }
    ],
}


def test_reviewer_flags_an_unsupported_promise() -> None:
    model = AsyncMock(
        return_value={
            "content": '{"verdict":"flagged","reason":"The evidence gives no delivery date."}'
        }
    )

    verdict = asyncio.run(
        ReviewerAgent(model).review("EMP-001", "I need my first work laptop.", APPROVAL)
    )

    assert verdict == {
        "verdict": "flagged",
        "reason": "The evidence gives no delivery date.",
    }


def test_reviewer_receives_fresh_context_with_only_evidence_and_no_tools() -> None:
    model = AsyncMock(
        return_value={"content": '{"verdict":"confirmed","reason":"Supported."}'}
    )

    asyncio.run(ReviewerAgent(model).review("EMP-001", "I need a laptop.", APPROVAL))

    messages, tools = model.await_args.args
    assert (
        [message["role"] for message in messages],
        json.loads(messages[1]["content"]),
        tools,
    ) == (
        ["system", "user"],
        {
            "employee_id": "EMP-001",
            "request": "I need a laptop.",
            "draft": DRAFT,
            "decision": APPROVAL["decision"],
            "observations": [
                {
                    "tool": "check_request_eligibility",
                    "arguments": {"employee_id": "EMP-001", "item": "laptop"},
                    "observation": APPROVAL["decision"],
                }
            ],
            "review_receipt": None,
        },
        [],
    )


@pytest.mark.parametrize(
    "turn",
    [
        {"content": "Done."},
        {"content": "[]"},
        {"content": None},
        {"content": '{"verdict":"approve","reason":"Supported."}'},
        {"content": '{"verdict":"confirmed","reason":" "}'},
        {
            "content": '{"verdict":"confirmed","reason":"Supported."}',
            "tool_calls": [{}],
        },
    ],
    ids=[
        "plain-text",
        "array",
        "empty",
        "invalid-verdict",
        "missing-reason",
        "tool-call",
    ],
)
def test_unusable_verdict_marks_review_unavailable(turn: dict) -> None:
    model = AsyncMock(return_value=turn)

    verdict = asyncio.run(
        ReviewerAgent(model).review("EMP-001", "I need a laptop.", APPROVAL)
    )

    assert verdict == {
        "verdict": "unavailable",
        "reason": "The reviewer returned an invalid verdict.",
    }


def test_model_timeout_marks_review_unavailable() -> None:
    model = AsyncMock(side_effect=TimeoutError)

    verdict = asyncio.run(
        ReviewerAgent(model).review("EMP-001", "I need a laptop.", APPROVAL)
    )

    assert verdict == {
        "verdict": "unavailable",
        "reason": "The reviewer could not verify the draft.",
    }


def test_reviewer_receives_retry_evidence_and_original_receipt() -> None:
    model = AsyncMock(
        return_value={"content": '{"verdict":"confirmed","reason":"Supported."}'}
    )
    receipt = {
        "request_id": "REQ-1",
        "review_id": "REV-1",
        "reason": "History unavailable.",
    }
    retry_step = {
        "tool": "get_employee_info",
        "arguments": {"employee_id": "EMP-001"},
        "observation": {"current_equipment": None},
    }
    result = {**APPROVAL, "retry_trace": [retry_step], "review": receipt}

    asyncio.run(ReviewerAgent(model).review("EMP-001", "I need a laptop.", result))

    evidence = json.loads(model.await_args.args[0][1]["content"])
    assert (evidence["retry_observations"], evidence["review_receipt"]) == (
        [retry_step],
        receipt,
    )
