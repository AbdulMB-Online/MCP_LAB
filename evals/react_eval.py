"""Evaluate the ReAct agent against policy, tool traces, and persisted reviews."""

import argparse
import asyncio
import json
import logging
from contextlib import closing
from pathlib import Path
from sqlite3 import connect
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from httpx2 import ASGITransport, AsyncClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool

from mcp_lab.agent import ChooseTurn, Message, _failure_message, run_react
from mcp_lab.foundry import FoundryModel
from mcp_lab.server.app import EquipmentTools, create_server

from .react_cases import CASES, Case, seed_evaluation_database

REVIEW_TOOL = "flag_for_human_review"
CHECKS = ("outcome", "reason", "tool_evidence", "review_persistence")


def expected_calls(case: Case) -> dict[str, dict]:
    if case.stop_after == "intake":
        return {}
    calls = {"get_employee_info": {"employee_id": case.employee}}
    if case.stop_after == "get_employee_info":
        return calls
    calls["get_policy_limits"] = {"role": case.role}
    if case.stop_after == "get_policy_limits":
        return calls
    arguments = {
        "employee_id": case.employee,
        "item": case.item,
        "request_kind": case.intent,
    }
    if case.asset:
        arguments["replacement_asset_id"] = case.asset
    calls["check_request_eligibility"] = arguments
    return calls


def scripted_model(calls: dict[str, dict]) -> ChooseTurn:
    turns = iter(enumerate(calls.items(), start=1))

    async def choose_turn(messages: list[Message], tools: list[Tool]) -> Message:
        index, (name, arguments) = next(turns)
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        }

    return choose_turn


def score_review(
    case: Case, result: dict, review_steps: list[dict], rows: list[tuple]
) -> bool:
    receipt = result.get("review")
    if case.outcome != "escalated":
        return receipt is None and not rows and not review_steps
    if not receipt or len(review_steps) != 1 or not receipt.get("review_id"):
        return False
    reason = result["decision"]["reason"]
    return (
        rows
        == [
            (
                result["request_id"],
                receipt["review_id"],
                case.employee,
                case.request,
                reason,
                receipt.get("created_at"),
            )
        ]
        and receipt["request_id"] == result["request_id"]
        and receipt["reason"] == reason
        and review_steps[0]["arguments"]
        == {
            "request_id": result["request_id"],
            "employee_id": case.employee,
            "request": case.request,
            "reason": reason,
        }
        and review_steps[0]["observation"] == receipt
    )


def score(
    case: Case, result: dict, rows: list[tuple], expected: dict
) -> dict[str, bool]:
    reason = result["decision"]["reason"].casefold()
    expected_reasons = (case.reason,) if isinstance(case.reason, str) else case.reason
    trace = result["trace"]
    reads = [step for step in trace if step["tool"] != REVIEW_TOOL]
    review_steps = [step for step in trace if step["tool"] == REVIEW_TOOL]
    evidence = (
        {step["tool"] for step in reads} == expected.keys()
        and all(step["arguments"] == expected[step["tool"]] for step in reads)
        and all(step["observation"] and step["rationale"] for step in trace)
        and all(
            step["observation"] == result["decision"]
            for step in reads
            if step["tool"] == "check_request_eligibility"
        )
    )
    return {
        "outcome": result["decision"]["outcome"] == case.outcome,
        "reason": any(fragment.casefold() in reason for fragment in expected_reasons),
        "tool_evidence": evidence,
        "review_persistence": score_review(case, result, review_steps, rows),
    }


async def run_case(
    case: Case, attempt: int, database: Path, model: FoundryModel | None
) -> dict[str, Any]:
    seed_evaluation_database(database, case)
    expected = expected_calls(case)
    choose_turn = model.complete if model is not None else scripted_model(expected)
    server = create_server(EquipmentTools(database, today=lambda: case.as_of))
    asgi = server.streamable_http_app(json_response=True, stateless_http=True)
    report: dict[str, Any] = {
        "case": case.name,
        "attempt": attempt,
        "evaluation_date": case.as_of.isoformat(),
        "employee_id": case.employee,
        "request": case.request,
        "expected_outcome": case.outcome,
        "expected_reason": case.reason,
        "expected_tool_arguments": expected,
    }
    started = perf_counter()
    async with (
        asgi.router.lifespan_context(asgi),
        AsyncClient(transport=ASGITransport(app=asgi)) as http,
    ):
        async with Client(
            streamable_http_client("http://127.0.0.1:8001/mcp", http_client=http),
            read_timeout_seconds=10,
        ) as client:
            try:
                async with asyncio.timeout(60):
                    result = await run_react(
                        client, choose_turn, case.employee, case.request
                    )
            except Exception as error:
                report["error"] = _failure_message(error)
                result = None

    with closing(connect(database)) as db:
        rows = db.execute(
            "SELECT request_id, review_id, employee_id, request_text, reason, created_at "
            "FROM human_reviews"
        ).fetchall()

    report["elapsed_ms"] = round((perf_counter() - started) * 1000)
    report["review_rows"] = len(rows)
    report["checks"] = (
        score(case, result, rows, expected)
        if result is not None
        else dict.fromkeys(CHECKS, False)
    )
    report["result"] = result
    return report


async def evaluate(cases: list[Case], *, live: bool, repetitions: int) -> int:
    reports = []
    with TemporaryDirectory(prefix="mcp-react-eval-") as directory:
        async with AsyncClient(timeout=15) as http:
            model = FoundryModel.from_environment(http) if live else None
            for case in cases:
                attempts = repetitions if case.repeat else 1
                for attempt in range(1, attempts + 1):
                    database = Path(directory) / f"{case.name}-{attempt}.sqlite3"
                    report = await run_case(case, attempt, database, model)
                    reports.append(report)
                    print(json.dumps(report), flush=True)

    def actual_outcome(report: dict) -> str | None:
        result = report["result"]
        return result["decision"]["outcome"] if result else None

    passed = sum(all(report["checks"].values()) for report in reports)
    print(
        json.dumps(
            {
                "summary": {
                    "mode": "live" if live else "scripted",
                    "cases": len(cases),
                    "runs": len(reports),
                    "passed": passed,
                    "errors": sum("error" in report for report in reports),
                    "checks_passed": {
                        check: sum(report["checks"][check] for report in reports)
                        for check in CHECKS
                    },
                    "unjustified_approvals": sum(
                        actual_outcome(report) == "approved"
                        and not all(report["checks"].values())
                        for report in reports
                    ),
                    "unnecessary_escalations": sum(
                        actual_outcome(report) == "escalated"
                        and report["expected_outcome"] != "escalated"
                        for report in reports
                    ),
                }
            }
        )
    )
    return int(passed != len(reports))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Use the configured Foundry model."
    )
    parser.add_argument(
        "--case", action="append", choices=[case.name for case in CASES]
    )
    parser.add_argument(
        "--repeat-boundaries", type=int, help="Runs per selected boundary case."
    )
    args = parser.parse_args()
    repetitions = args.repeat_boundaries
    if repetitions is None:
        repetitions = 3 if args.live else 1
    if repetitions < 1:
        parser.error("--repeat-boundaries must be at least 1.")

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)
    cases = [case for case in CASES if args.case is None or case.name in args.case]
    return asyncio.run(evaluate(cases, live=args.live, repetitions=repetitions))


if __name__ == "__main__":
    raise SystemExit(main())
