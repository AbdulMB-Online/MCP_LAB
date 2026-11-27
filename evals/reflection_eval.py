"""Replay paired correct and flawed drafts against the live Foundry reviewer."""

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Literal

from httpx2 import AsyncClient

from mcp_lab.foundry import FoundryModel
from mcp_lab.reviewer import ReviewerAgent

OUTCOMES = {
    "first-laptop": "approved",
    "console-standard": "denied",
    "replacement-day-before": "escalated",
    "unavailable-history": "escalated",
}
BASELINE_CHECKS = ("outcome", "reason", "tool_evidence", "review_persistence")


@dataclass(frozen=True)
class DraftCase:
    name: str
    employee_id: str
    request: str
    result: dict
    expected_verdict: Literal["confirmed", "flagged"]


def _flawed_draft(name: str, result: dict) -> str:
    draft = result["draft"]
    if name == "first-laptop":
        return f"{draft} Your laptop will arrive Friday."
    if name == "console-standard":
        return "Approved. You are eligible for the gaming console."
    if name == "replacement-day-before":
        if "2028-01-15" not in draft:
            raise ValueError(
                "The replacement baseline must contain the original due date."
            )
        return draft.replace("2028-01-15", "2028-01-16")
    review_id = result["review"]["review_id"]
    last_digit = "0" if review_id.endswith("1") else "1"
    wrong_id = review_id[:-1] + last_digit
    return f"{draft} Your review ID is {wrong_id}."


def build_cases(baseline: list[dict]) -> list[DraftCase]:
    cases: list[DraftCase] = []
    for name, outcome in OUTCOMES.items():
        matches = [
            row
            for row in baseline
            if row.get("case") == name and row.get("attempt") == 1
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one first attempt for {name!r} in the baseline."
            )
        (row,) = matches
        result = row.get("result")
        checks = row.get("checks", {})
        if (
            not result
            or result["decision"]["outcome"] != outcome
            or not result.get("draft")
            or not all(checks.get(check) is True for check in BASELINE_CHECKS)
            or (
                outcome == "escalated" and not result.get("review", {}).get("review_id")
            )
        ):
            raise ValueError(
                f"The baseline for {name!r} must contain a passing ReAct result."
            )
        cases.extend(
            [
                DraftCase(
                    name, row["employee_id"], row["request"], result, "confirmed"
                ),
                DraftCase(
                    name,
                    row["employee_id"],
                    row["request"],
                    {**result, "draft": _flawed_draft(name, result)},
                    "flagged",
                ),
            ]
        )
    return cases


async def evaluate(cases: list[DraftCase], reviewer: ReviewerAgent) -> list[dict]:
    rows = []
    for case in cases:
        start = perf_counter()
        verdict = await reviewer.review(case.employee_id, case.request, case.result)
        rows.append(
            {
                "case": case.name,
                "variant": "supported"
                if case.expected_verdict == "confirmed"
                else "flawed",
                "employee_id": case.employee_id,
                "request": case.request,
                "draft": case.result["draft"],
                "expected_verdict": case.expected_verdict,
                **verdict,
                "elapsed_ms": round((perf_counter() - start) * 1000),
            }
        )
    return rows


def summarize(rows: list[dict]) -> dict:
    correct = [row for row in rows if row["expected_verdict"] == "confirmed"]
    flawed = [row for row in rows if row["expected_verdict"] == "flagged"]
    correct_released = sum(row["verdict"] == "confirmed" for row in correct)
    flawed_flagged = sum(row["verdict"] == "flagged" for row in flawed)
    return {
        "cases": len(rows),
        "passed": correct_released + flawed_flagged,
        "without_reflection": {
            "correct_released": len(correct),
            "flawed_released": len(flawed),
        },
        "with_reflection": {
            "correct_released": correct_released,
            "correct_withheld": len(correct) - correct_released,
            "flawed_released": sum(row["verdict"] == "confirmed" for row in flawed),
            "flawed_flagged": flawed_flagged,
            "unverified": sum(
                row["verdict"] in ("unavailable", "needs_evidence") for row in rows
            ),
        },
        "median_review_ms": round(median(row["elapsed_ms"] for row in rows)),
    }


async def _run_live(cases: list[DraftCase]) -> list[dict]:
    async with AsyncClient(timeout=15) as http:
        model = FoundryModel.from_environment(http)
        return await evaluate(cases, ReviewerAgent(model.complete))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, default=Path("output/react-eval-live.jsonl")
    )
    args = parser.parse_args()
    try:
        baseline = [
            json.loads(line)
            for line in args.baseline.read_text(encoding="utf-8").splitlines()
        ]
        cases = build_cases(baseline)
        rows = asyncio.run(_run_live(cases))
    except (OSError, ValueError) as error:
        parser.exit(2, f"Could not run the reflection eval. {error}\n")
    for row in rows:
        print(json.dumps(row))
    summary = summarize(rows)
    print(json.dumps({"summary": summary, "baseline": str(args.baseline)}))
    return int(summary["passed"] != len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
