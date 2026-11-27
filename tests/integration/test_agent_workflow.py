"""Run the agent through the real Streamable HTTP tools and review database."""

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult

from mcp_lab.agent import run_react, run_tool_loop
from mcp_lab.reviewer import ReviewerAgent
from mcp_lab.server.app import EquipmentTools, create_server
from mcp_lab.server.database import initialize_database

REQUEST_ID = "REQ-WORKFLOW"
FIRST_LAPTOP = "Can I get my first work laptop?"


def _turn(name: str, arguments: dict) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": f"call-{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def _eligibility(employee: str, item: str, kind: str, **extra: str) -> dict:
    return _turn(
        "check_request_eligibility",
        {
            "employee_id": employee,
            "item": item,
            "request_kind": kind,
            **extra,
        },
    )


@pytest.fixture
def workflow_database(tmp_path: Path) -> Path:
    path = tmp_path / "workflow.sqlite3"
    initialize_database(path, seed=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "INSERT INTO employees VALUES ('EMP-NO-POLICY', 'intern', '2022-01-01', 'complete')"
        )
    return path


@pytest.fixture
def workflow_client(workflow_database: Path, mcp_http_client):
    tools = EquipmentTools(workflow_database, today=lambda: date(2026, 11, 25))
    server = create_server(tools)
    return lambda: mcp_http_client(server)


def _run(workflow_client, employee: str, request_text: str, *turns: dict) -> dict:
    async def run() -> dict:
        async with workflow_client() as client:
            model = AsyncMock(side_effect=turns)
            return await run_react(
                client, model, employee, request_text, request_id=REQUEST_ID
            )

    return asyncio.run(run())


def _reviews(path: Path) -> list[tuple]:
    with closing(sqlite3.connect(path)) as db:
        return db.execute(
            "SELECT review_id, request_id, employee_id, request_text, reason FROM human_reviews"
        ).fetchall()


@pytest.mark.parametrize(
    ("request_text", "item", "kind", "outcome", "label"),
    [
        (FIRST_LAPTOP, "laptop", "first_issue", "approved", "Approved."),
        (
            "I need a gaming console for work.",
            "gaming console",
            "unknown",
            "denied",
            "Denied.",
        ),
    ],
    ids=["first-laptop", "prohibited-console"],
)
def test_agent_returns_a_confirmed_policy_decision(
    workflow_client,
    workflow_database: Path,
    request_text: str,
    item: str,
    kind: str,
    outcome: str,
    label: str,
) -> None:
    result = _run(
        workflow_client, "EMP-001", request_text, _eligibility("EMP-001", item, kind)
    )

    assert (
        result["decision"]["outcome"],
        result["draft"].split()[0],
        _reviews(workflow_database),
    ) == (outcome, label, [])


@pytest.mark.parametrize(
    ("employee", "request_text", "turn", "reason"),
    [
        (
            "EMP-002",
            "Replace my damaged laptop LAP-002.",
            _eligibility(
                "EMP-002", "laptop", "replacement", replacement_asset_id="LAP-002"
            ),
            "not due for replacement",
        ),
        (
            "EMP-003",
            FIRST_LAPTOP,
            _eligibility("EMP-003", "laptop", "first_issue"),
            "Equipment history is unavailable",
        ),
    ],
    ids=["early-replacement", "missing-history"],
)
def test_agent_files_escalations_with_the_actual_reason(
    workflow_client,
    workflow_database: Path,
    employee: str,
    request_text: str,
    turn: dict,
    reason: str,
) -> None:
    result = _run(workflow_client, employee, request_text, turn)
    receipt = result["review"]

    assert (
        result["decision"]["outcome"],
        result["draft"].startswith("This request was filed for human review."),
        reason in receipt["reason"],
        _reviews(workflow_database),
    ) == (
        "escalated",
        True,
        True,
        [
            (
                receipt["review_id"],
                REQUEST_ID,
                employee,
                request_text,
                result["decision"]["reason"],
            )
        ],
    )


@pytest.mark.parametrize(
    ("employee", "turns", "error_code"),
    [
        (
            "EMP-999",
            [_turn("get_employee_info", {"employee_id": "EMP-999"})],
            "employee_not_found",
        ),
        (
            "EMP-NO-POLICY",
            [
                _turn("get_employee_info", {"employee_id": "EMP-NO-POLICY"}),
                _turn("get_policy_limits", {"role": "intern"}),
            ],
            "policy_not_found",
        ),
    ],
    ids=["unknown-employee", "unconfigured-role"],
)
def test_expected_lookup_failure_is_filed_for_review(
    workflow_client,
    workflow_database: Path,
    employee: str,
    turns: list[dict],
    error_code: str,
) -> None:
    result = _run(workflow_client, employee, FIRST_LAPTOP, *turns)
    receipt = result["review"]

    assert (
        result["trace"][-2]["observation"]["error"],
        _reviews(workflow_database),
    ) == (
        error_code,
        [(receipt["review_id"], REQUEST_ID, employee, FIRST_LAPTOP, receipt["reason"])],
    )


def test_lost_review_response_retries_one_actual_write(
    workflow_client, workflow_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_receipt = {}

    async def run() -> dict:
        async with workflow_client() as client:
            original_call = client.call_tool

            async def lose_first_review_receipt(
                name: str, arguments: dict, **kwargs
            ) -> CallToolResult:
                response = await original_call(name, arguments, **kwargs)
                if name == "flag_for_human_review" and not first_receipt:
                    first_receipt.update(response.structured_content)
                    raise TimeoutError("Response lost after the server committed")
                return response

            monkeypatch.setattr(client, "call_tool", lose_first_review_receipt)
            model = AsyncMock(
                side_effect=[
                    _eligibility("EMP-003", "laptop", "first_issue"),
                ]
            )
            return await run_react(client, model, "EMP-003", FIRST_LAPTOP)

    result = asyncio.run(run())
    receipt = result["review"]

    assert (receipt, _reviews(workflow_database)) == (
        first_receipt,
        [
            (
                receipt["review_id"],
                result["request_id"],
                "EMP-003",
                FIRST_LAPTOP,
                receipt["reason"],
            )
        ],
    )


def test_lookup_outage_fails_processing(tmp_path: Path, mcp_http_client) -> None:
    server = create_server(EquipmentTools(tmp_path / "unavailable.sqlite3"))

    async def run() -> None:
        async with mcp_http_client(server) as client:
            model = AsyncMock(
                side_effect=[_turn("get_employee_info", {"employee_id": "EMP-001"})]
            )
            with pytest.raises(ValueError, match="get_employee_info.*failed"):
                await run_react(client, model, "EMP-001", FIRST_LAPTOP)

    asyncio.run(run())


def test_partial_history_with_future_asset_blocks_both_tools(
    workflow_client,
    workflow_database: Path,
) -> None:
    with closing(sqlite3.connect(workflow_database)) as db, db:
        db.execute(
            "INSERT INTO equipment_assets VALUES (?, ?, ?, ?, ?)",
            ("MON-FUT", "EMP-003", "monitor", "9990-01-01", "active"),
        )

    async def observe() -> tuple[CallToolResult, CallToolResult]:
        async with workflow_client() as client:
            lookup = await client.call_tool(
                "get_employee_info", {"employee_id": "EMP-003"}
            )
            policy = await client.call_tool(
                "check_request_eligibility",
                {"employee_id": "EMP-003", "item": "gaming console"},
            )
            return lookup, policy

    lookup, policy = asyncio.run(observe())

    assert (
        lookup.is_error,
        (lookup.meta or {}).get("equipment_error"),
        policy.structured_content,
    ) == (
        True,
        "invalid_employee_dates",
        {
            "outcome": "escalated",
            "reason": "The equipment history contains a future issue date.",
        },
    )


def test_wrong_item_cannot_turn_a_policy_denial_into_an_approval(
    workflow_client,
    workflow_database: Path,
) -> None:
    request_text = "I need a gaming console for work."

    result = _run(
        workflow_client,
        "EMP-001",
        request_text,
        _eligibility("EMP-001", "laptop", "first_issue"),
    )
    receipt = result["review"]

    assert (result["decision"]["outcome"], _reviews(workflow_database)) == (
        "escalated",
        [
            (
                receipt["review_id"],
                REQUEST_ID,
                "EMP-001",
                request_text,
                "The proposed equipment details do not match the request.",
            )
        ],
    )


def test_reviewer_retry_preserves_the_committed_receipt(
    workflow_client, workflow_database: Path
) -> None:
    eligibility_turn = _eligibility("EMP-003", "laptop", "first_issue")
    equipment_model = AsyncMock(side_effect=[eligibility_turn, eligibility_turn])
    reviewer_model = AsyncMock(
        side_effect=[
            {
                "content": '{"verdict":"needs_evidence","reason":"Recheck equipment history."}'
            },
            {
                "content": '{"verdict":"confirmed","reason":"The original receipt is supported."}'
            },
        ]
    )

    async def run() -> dict:
        async with workflow_client() as client:
            return await run_tool_loop(
                client,
                equipment_model,
                "EMP-003",
                FIRST_LAPTOP,
                request_id=REQUEST_ID,
                reviewer=ReviewerAgent(reviewer_model),
            )

    result = asyncio.run(run())
    receipt = result["review"]
    retry_message = json.loads(equipment_model.await_args.args[0][-1]["content"])

    assert (
        retry_message["reviewer_feedback"]["reason"],
        result["reflection"],
        [step["tool"] for step in result["retry_trace"]],
        _reviews(workflow_database),
    ) == (
        "Recheck equipment history.",
        "confirmed",
        ["check_request_eligibility"],
        [
            (
                receipt["review_id"],
                REQUEST_ID,
                "EMP-003",
                FIRST_LAPTOP,
                receipt["reason"],
            )
        ],
    )
