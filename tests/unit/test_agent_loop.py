"""Exercise the decision loop with scripted model turns and MCP responses."""

import asyncio
import json
from unittest.mock import AsyncMock, call

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INTERNAL_ERROR,
    REQUEST_TIMEOUT,
    CallToolResult,
    ListToolsResult,
    Tool,
)

from mcp_lab.agent import run_react, run_tool_loop
from mcp_lab.reviewer import ReviewerAgent

EMPLOYEE_ID = "EMP-001"
REQUEST = "Can I get my first work laptop?"
FIRST_LAPTOP = {
    "employee_id": EMPLOYEE_ID,
    "item": "laptop",
    "request_kind": "first_issue",
}
LOOKUP = "get_employee_info"
POLICY = "get_policy_limits"
ELIGIBILITY = "check_request_eligibility"
REVIEW = "flag_for_human_review"


def _tool_turn(name: str, arguments: dict[str, str], call_id: str = "call-1") -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def _result(**fields: object) -> CallToolResult:
    return CallToolResult(content=[], structured_content=fields)


@pytest.fixture
def mcp_client() -> AsyncMock:
    client = AsyncMock(spec=Client)
    client.list_tools.return_value = ListToolsResult(
        tools=[
            Tool(name=name, inputSchema={"type": "object"})
            for name in (LOOKUP, POLICY, ELIGIBILITY, REVIEW)
        ]
    )
    return client


@pytest.fixture
def completed_request(mcp_client: AsyncMock) -> tuple:
    employee = _result(role="standard", current_equipment=[])
    policy = _result(laptop={"max_active_quantity": 1})
    decision = _result(outcome="approved", reason="First issue is within the limit.")
    mcp_client.call_tool.side_effect = [employee, policy, decision]
    model = AsyncMock(
        side_effect=[
            _tool_turn(LOOKUP, {"employee_id": EMPLOYEE_ID}),
            _tool_turn(POLICY, {"role": "standard"}, "call-2"),
            _tool_turn(
                ELIGIBILITY,
                {
                    "employee_id": EMPLOYEE_ID,
                    "item": "laptop",
                    "request_kind": "first_issue",
                },
                "call-3",
            ),
        ]
    )
    result = asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST))
    return model, mcp_client, result


@pytest.mark.parametrize(
    ("turn_index", "call_id", "observation"),
    [
        (1, "call-1", {"role": "standard", "current_equipment": []}),
        (2, "call-2", {"laptop": {"max_active_quantity": 1}}),
    ],
    ids=["employee", "policy"],
)
def test_model_receives_actual_tool_result(
    completed_request: tuple, turn_index: int, call_id: str, observation: dict
) -> None:
    model, _, _ = completed_request

    messages = model.await_args_list[turn_index].args[0]

    assert messages[-1] == {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(observation),
    }


def test_trace_records_action_rationale(completed_request: tuple) -> None:
    _, _, result = completed_request

    assert result["trace"][0]["rationale"] == (
        "Verify the employee's recorded role and equipment."
    )


def test_only_read_tools_are_available_to_model(completed_request: tuple) -> None:
    model, _, _ = completed_request
    tools = model.await_args_list[0].args[1]
    assert {tool.name for tool in tools} == {LOOKUP, POLICY, ELIGIBILITY}


def test_dispatched_tool_sequence_is_recorded(completed_request: tuple) -> None:
    _, _, result = completed_request
    assert [step["tool"] for step in result["trace"]] == [LOOKUP, POLICY, ELIGIBILITY]


def test_agent_dispatches_model_arguments(completed_request: tuple) -> None:
    _, client, _ = completed_request
    assert client.call_tool.await_args_list == [
        call(LOOKUP, {"employee_id": EMPLOYEE_ID}),
        call(POLICY, {"role": "standard"}),
        call(
            ELIGIBILITY,
            {
                "employee_id": EMPLOYEE_ID,
                "item": "laptop",
                "request_kind": "first_issue",
            },
        ),
    ]


def test_model_receives_original_request(completed_request: tuple) -> None:
    model, _, _ = completed_request
    messages = model.await_args_list[0].args[0]
    assert json.loads(messages[-1]["content"]) == {
        "request": REQUEST,
        "verified_arguments": FIRST_LAPTOP,
    }


def test_early_model_approval_cannot_bypass_eligibility(mcp_client: AsyncMock) -> None:
    # Arrange
    model = AsyncMock(return_value={"role": "assistant", "content": "Approved!"})

    # Act / Assert
    with pytest.raises(ValueError, match="before checking eligibility"):
        asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST))


def test_final_outcome_uses_mcp_decision(mcp_client: AsyncMock) -> None:
    # Arrange
    decision = _result(outcome="denied", reason="The role policy prohibits this item.")
    mcp_client.call_tool.return_value = decision
    model = AsyncMock(
        side_effect=[
            _tool_turn(
                ELIGIBILITY, {"employee_id": EMPLOYEE_ID, "item": "gaming console"}
            ),
        ]
    )

    # Act
    result = asyncio.run(
        run_react(mcp_client, model, EMPLOYEE_ID, "I need a gaming console for work.")
    )

    # Assert
    assert result["draft"] == "Denied. The role policy prohibits this item."


def test_review_action_is_rejected_during_policy_evaluation(
    mcp_client: AsyncMock,
) -> None:
    # Arrange
    mcp_client.call_tool.side_effect = AssertionError("Unexpected review dispatch")
    model = AsyncMock(return_value=_tool_turn(REVIEW, {"employee_id": EMPLOYEE_ID}))

    # Act / Assert
    with pytest.raises(ValueError, match="unavailable tool"):
        asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST))


def test_model_cannot_change_employee_id(mcp_client: AsyncMock) -> None:
    # Arrange
    mcp_client.call_tool.return_value = _review_result(
        "The proposed employee does not match the request."
    )
    model = AsyncMock(side_effect=[_tool_turn(LOOKUP, {"employee_id": "EMP-999"})])

    asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST, request_id="REQ-1"))

    assert mcp_client.call_tool.await_args.args[0] == REVIEW


def test_invalid_tool_arguments_are_rejected_before_dispatch(
    mcp_client: AsyncMock,
) -> None:
    # Arrange
    mcp_client.call_tool.side_effect = AssertionError(
        "Unexpected malformed call dispatch"
    )
    turn = _tool_turn(LOOKUP, {"employee_id": EMPLOYEE_ID})
    turn["tool_calls"][0]["function"]["arguments"] = '["EMP-001"]'
    model = AsyncMock(return_value=turn)

    # Act / Assert
    with pytest.raises(ValueError, match="malformed tool call"):
        asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST))


def test_incomplete_eligibility_result_cannot_produce_draft(
    mcp_client: AsyncMock,
) -> None:
    # Arrange
    mcp_client.call_tool.return_value = _result(outcome="approved")
    model = AsyncMock(return_value=_tool_turn(ELIGIBILITY, FIRST_LAPTOP))

    # Act / Assert
    with pytest.raises(ValueError, match="returned no reason"):
        asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST))


def test_tool_error_stops_before_another_model_turn(mcp_client: AsyncMock) -> None:
    # Arrange
    mcp_client.call_tool.return_value = CallToolResult(content=[], isError=True)
    model = AsyncMock(
        side_effect=[
            _tool_turn(LOOKUP, {"employee_id": EMPLOYEE_ID}),
            AssertionError("The loop continued after an MCP error"),
        ]
    )

    # Act / Assert
    with pytest.raises(ValueError, match="get_employee_info.*failed"):
        asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST))


def test_turn_limit_stops_repeated_tool_calls(mcp_client: AsyncMock) -> None:
    # Arrange
    lookup_args = {"employee_id": EMPLOYEE_ID}
    mcp_client.call_tool.return_value = _result(role="standard")
    model = AsyncMock(
        side_effect=[
            _tool_turn(LOOKUP, lookup_args, "call-1"),
            _tool_turn(LOOKUP, lookup_args, "call-2"),
            AssertionError("The loop exceeded its turn limit"),
        ]
    )

    # Act / Assert
    with pytest.raises(ValueError, match="turn limit"):
        asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST, max_turns=2))


def test_missing_eligibility_tool_blocks_model(mcp_client: AsyncMock) -> None:
    mcp_client.list_tools.return_value = ListToolsResult(tools=[])
    model = AsyncMock()

    with pytest.raises(ValueError, match="does not expose eligibility"):
        asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST))


def test_invalid_eligibility_outcome_is_rejected(mcp_client: AsyncMock) -> None:
    mcp_client.call_tool.return_value = _result(
        outcome="shipped", reason="Invalid outcome"
    )
    model = AsyncMock(return_value=_tool_turn(ELIGIBILITY, FIRST_LAPTOP))

    with pytest.raises(ValueError, match="invalid outcome"):
        asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST))


@pytest.mark.parametrize(
    ("calls", "error"),
    [
        ([{}, {}], "exactly one tool"),
        ([{}], "malformed tool call"),
    ],
    ids=["parallel-calls", "missing-fields"],
)
def test_invalid_tool_turn_is_rejected(
    mcp_client: AsyncMock, calls: list[dict], error: str
) -> None:
    model = AsyncMock(return_value={"tool_calls": calls})

    with pytest.raises(ValueError, match=error):
        asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST))


def _review_result(reason: str) -> CallToolResult:
    return _result(
        outcome="escalated",
        request_id="REQ-1",
        review_id="REV-1",
        reason=reason,
        created_at="2026-11-25T00:00:00Z",
    )


@pytest.mark.parametrize(
    ("request_text", "proposed"),
    [
        ("I need a gaming console for work.", FIRST_LAPTOP),
        (
            REQUEST,
            {**FIRST_LAPTOP, "item": "gaming console", "request_kind": "unknown"},
        ),
        (REQUEST, {**FIRST_LAPTOP, "request_kind": "additional"}),
        (
            "Replace my laptop LAP-002.",
            {
                **FIRST_LAPTOP,
                "request_kind": "replacement",
                "replacement_asset_id": "LAP-009",
            },
        ),
    ],
    ids=["wrong-item-approval", "wrong-item-denial", "wrong-intent", "wrong-asset"],
)
def test_mismatched_equipment_is_escalated(
    mcp_client: AsyncMock, request_text: str, proposed: dict[str, str]
) -> None:
    reason = "The proposed equipment details do not match the request."
    mcp_client.call_tool.return_value = _review_result(reason)
    model = AsyncMock(side_effect=[_tool_turn(ELIGIBILITY, proposed)])

    asyncio.run(
        run_react(mcp_client, model, EMPLOYEE_ID, request_text, request_id="REQ-1")
    )

    assert mcp_client.call_tool.await_args_list == [
        call(
            REVIEW,
            {
                "employee_id": EMPLOYEE_ID,
                "request": request_text,
                "reason": reason,
                "request_id": "REQ-1",
            },
            read_timeout_seconds=8,
        )
    ]


@pytest.mark.parametrize(
    "text",
    [
        "I need a laptop and a monitor.",
        "I need a laptop.",
        "Replace my damaged laptop.",
    ],
)
def test_unclear_request_is_filed_without_asking_model_for_tool(
    mcp_client: AsyncMock, text: str
) -> None:
    mcp_client.call_tool.return_value = _review_result(
        "The request does not identify one supported item and intent."
    )
    model = AsyncMock(side_effect=AssertionError("Unexpected model turn"))

    result = asyncio.run(
        run_react(mcp_client, model, EMPLOYEE_ID, text, request_id="REQ-1")
    )

    assert result["review"]["review_id"] == "REV-1"


def test_unverified_policy_role_is_escalated(mcp_client: AsyncMock) -> None:
    mcp_client.call_tool.side_effect = [
        _result(role="standard"),
        _review_result("The proposed role does not match a verified employee record."),
    ]
    model = AsyncMock(
        side_effect=[
            _tool_turn(LOOKUP, {"employee_id": EMPLOYEE_ID}),
            _tool_turn(POLICY, {"role": "manager"}),
        ]
    )

    result = asyncio.run(
        run_react(mcp_client, model, EMPLOYEE_ID, REQUEST, request_id="REQ-1")
    )

    assert [step["tool"] for step in result["trace"]] == [LOOKUP, REVIEW]


def test_react_baseline_returns_the_draft_after_eligibility(
    mcp_client: AsyncMock,
) -> None:
    mcp_client.call_tool.return_value = _result(
        outcome="approved", reason="Within policy."
    )
    model = AsyncMock(side_effect=[_tool_turn(ELIGIBILITY, FIRST_LAPTOP)])

    result = asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, REQUEST))

    assert (result["draft"], model.await_count) == ("Approved. Within policy.", 1)


def test_failed_review_cannot_be_reported_as_filed(mcp_client: AsyncMock) -> None:
    mcp_client.call_tool.return_value = CallToolResult(content=[], is_error=True)
    model = AsyncMock(
        side_effect=AssertionError("The agent attempted to confirm a draft.")
    )

    with pytest.raises(ValueError, match="did not confirm"):
        asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, "I need a laptop."))


@pytest.mark.parametrize(
    ("tool", "code", "reason"),
    [
        (
            "get_employee_info",
            "employee_not_found",
            "The employee could not be verified.",
        ),
        (
            "get_employee_info",
            "invalid_employee_dates",
            "The employee record contains an invalid date.",
        ),
        (
            "get_policy_limits",
            "policy_not_found",
            "No policy covers the employee's role.",
        ),
    ],
)
def test_business_lookup_error_files_review(
    mcp_client: AsyncMock, tool: str, code: str, reason: str
) -> None:
    lookup = _tool_turn(LOOKUP, {"employee_id": EMPLOYEE_ID})
    error = CallToolResult(content=[], is_error=True, meta={"equipment_error": code})
    if tool == POLICY:
        turns = [lookup, _tool_turn(POLICY, {"role": "standard"})]
        responses = [_result(role="standard"), error, _review_result(reason)]
    else:
        turns = [lookup]
        responses = [error, _review_result(reason)]
    mcp_client.call_tool.side_effect = responses
    model = AsyncMock(side_effect=turns)

    result = asyncio.run(
        run_react(mcp_client, model, EMPLOYEE_ID, REQUEST, request_id="REQ-1")
    )

    assert result["review"]["reason"] == reason


def test_review_retries_an_mcp_request_timeout(mcp_client: AsyncMock) -> None:
    reason = "The request does not identify one supported item and intent."
    mcp_client.call_tool.side_effect = [
        MCPError(code=REQUEST_TIMEOUT, message="Timed out"),
        _review_result(reason),
    ]
    model = AsyncMock(side_effect=AssertionError("Unexpected model turn"))

    asyncio.run(
        run_react(
            mcp_client, model, EMPLOYEE_ID, "I need a laptop.", request_id="REQ-1"
        )
    )

    first_call, retry_call = mcp_client.call_tool.await_args_list
    assert retry_call == first_call


def test_review_propagates_an_mcp_server_failure(mcp_client: AsyncMock) -> None:
    mcp_client.call_tool.side_effect = [
        MCPError(code=INTERNAL_ERROR, message="Database unavailable"),
        AssertionError("Retried an unknown server failure"),
    ]

    with pytest.raises(MCPError, match="Database unavailable"):
        asyncio.run(run_react(mcp_client, AsyncMock(), EMPLOYEE_ID, "I need a laptop."))


@pytest.mark.parametrize(
    ("employee_id", "request_text", "request_id", "error"),
    [
        (EMPLOYEE_ID, REQUEST, " ", "Request ID"),
        (" ", REQUEST, "REQ-1", "Employee ID and request"),
        (EMPLOYEE_ID, " ", "REQ-1", "Employee ID and request"),
    ],
)
def test_rejects_blank_intake_fields(
    mcp_client: AsyncMock,
    employee_id: str,
    request_text: str,
    request_id: str,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        asyncio.run(
            run_react(
                mcp_client,
                AsyncMock(),
                employee_id,
                request_text,
                request_id=request_id,
            )
        )


def test_additional_monitor_uses_the_explicit_request_intent(
    mcp_client: AsyncMock,
) -> None:
    arguments = {
        "employee_id": EMPLOYEE_ID,
        "item": "monitor",
        "request_kind": "additional",
    }
    mcp_client.call_tool.return_value = _result(
        outcome="approved", reason="Within policy."
    )
    model = AsyncMock(side_effect=[_tool_turn(ELIGIBILITY, arguments)])

    asyncio.run(run_react(mcp_client, model, EMPLOYEE_ID, "I need a second monitor."))

    mcp_client.call_tool.assert_awaited_once_with(ELIGIBILITY, arguments)


CONFIRMED = {
    "role": "assistant",
    "content": '{"verdict":"confirmed","reason":"Supported by the evidence."}',
}


def test_flagged_draft_is_withheld(mcp_client: AsyncMock) -> None:
    mcp_client.call_tool.return_value = _result(
        outcome="approved", reason="Within policy."
    )
    model = AsyncMock(
        side_effect=[
            _tool_turn(ELIGIBILITY, FIRST_LAPTOP),
            {
                "role": "assistant",
                "content": '{"verdict":"flagged","reason":"The draft overstates the evidence."}',
            },
        ]
    )

    result = asyncio.run(run_tool_loop(mcp_client, model, EMPLOYEE_ID, REQUEST))

    assert (result["draft"], result["reflection_reason"], model.await_count) == (
        None,
        "The draft overstates the evidence.",
        2,
    )


MORE_EVIDENCE = {
    "content": '{"verdict":"needs_evidence","reason":"Verify the equipment history."}'
}


def test_second_request_for_more_evidence_withholds_draft(
    mcp_client: AsyncMock,
) -> None:
    mcp_client.call_tool.return_value = _result(
        outcome="approved", reason="Within policy."
    )
    equipment_model = AsyncMock(side_effect=[_tool_turn(ELIGIBILITY, FIRST_LAPTOP)] * 2)
    review_model = AsyncMock(side_effect=[MORE_EVIDENCE, MORE_EVIDENCE])

    result = asyncio.run(
        run_tool_loop(
            mcp_client,
            equipment_model,
            EMPLOYEE_ID,
            REQUEST,
            reviewer=ReviewerAgent(review_model),
        )
    )

    assert (
        result["reflection"],
        result["draft"],
        equipment_model.await_count,
        review_model.await_count,
    ) == ("needs_evidence", None, 2, 2)


def test_new_escalation_after_retry_uses_the_original_request_id(
    mcp_client: AsyncMock,
) -> None:
    mcp_client.call_tool.side_effect = [
        _result(outcome="approved", reason="Within policy."),
        _result(outcome="escalated", reason="Equipment history is unavailable."),
        _review_result("Equipment history is unavailable."),
    ]
    equipment_model = AsyncMock(side_effect=[_tool_turn(ELIGIBILITY, FIRST_LAPTOP)] * 2)
    review_model = AsyncMock(side_effect=[MORE_EVIDENCE, CONFIRMED])

    result = asyncio.run(
        run_tool_loop(
            mcp_client,
            equipment_model,
            EMPLOYEE_ID,
            REQUEST,
            request_id="REQ-1",
            reviewer=ReviewerAgent(review_model),
        )
    )

    assert (
        result["decision"]["outcome"],
        result["review"]["request_id"],
        result["reflection"],
    ) == ("escalated", "REQ-1", "confirmed")


@pytest.mark.parametrize(
    "failure_at", ["review", "retry"], ids=["review-timeout", "retry-timeout"]
)
def test_reflection_timeout_keeps_the_committed_receipt(
    mcp_client: AsyncMock, failure_at: str
) -> None:
    reason = "Equipment history is unavailable."
    mcp_client.call_tool.side_effect = [
        _result(outcome="escalated", reason=reason),
        _review_result(reason),
    ]
    equipment_model = AsyncMock(
        side_effect=[_tool_turn(ELIGIBILITY, FIRST_LAPTOP), TimeoutError()]
    )
    review_model = AsyncMock(
        side_effect=[TimeoutError() if failure_at == "review" else MORE_EVIDENCE]
    )

    result = asyncio.run(
        run_tool_loop(
            mcp_client,
            equipment_model,
            EMPLOYEE_ID,
            REQUEST,
            request_id="REQ-1",
            reviewer=ReviewerAgent(review_model),
        )
    )

    assert (result["review"]["review_id"], result["reflection"], result["draft"]) == (
        "REV-1",
        "unavailable",
        None,
    )


def test_request_with_no_supported_intent_cannot_trigger_a_tool_retry(
    mcp_client: AsyncMock,
) -> None:
    mcp_client.call_tool.return_value = _review_result(
        "The request does not identify one supported item and intent."
    )
    equipment_model = AsyncMock(side_effect=AssertionError("Unexpected model turn"))
    review_model = AsyncMock(side_effect=[MORE_EVIDENCE])

    result = asyncio.run(
        run_tool_loop(
            mcp_client,
            equipment_model,
            EMPLOYEE_ID,
            "I need a laptop.",
            request_id="REQ-1",
            reviewer=ReviewerAgent(review_model),
        )
    )

    assert (result["reflection"], result["draft"], equipment_model.await_count) == (
        "needs_evidence",
        None,
        0,
    )


def test_read_only_retry_does_not_replace_a_committed_escalation(
    mcp_client: AsyncMock,
) -> None:
    original = {"outcome": "escalated", "reason": "Equipment history is unavailable."}
    mcp_client.call_tool.side_effect = [
        _result(**original),
        _review_result(original["reason"]),
        _result(outcome="approved", reason="Within policy."),
    ]
    equipment_model = AsyncMock(side_effect=[_tool_turn(ELIGIBILITY, FIRST_LAPTOP)] * 2)
    review_model = AsyncMock(side_effect=[MORE_EVIDENCE, CONFIRMED])

    result = asyncio.run(
        run_tool_loop(
            mcp_client,
            equipment_model,
            EMPLOYEE_ID,
            REQUEST,
            request_id="REQ-1",
            reviewer=ReviewerAgent(review_model),
        )
    )

    assert (
        result["decision"],
        result["review"]["review_id"],
        mcp_client.call_tool.await_count,
    ) == (original, "REV-1", 3)
