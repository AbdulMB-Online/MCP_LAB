# Requirements and Design

Draft: request fields and role limits are agreed; remaining rules await review.

## Scope and Data

Draft equipment decisions using synthetic data. Foundry supplies only the model; our application enforces policies. Equipment is not purchased or issued; escalations are persisted.

- **Request:** employee ID, one item, and business reason; system-generated request ID and UTC timestamp.
- **Employee:** authoritative role, start date, and equipment history. Derive tenure; no minimum tenure applies.
- **Equipment:** asset ID, item type, issue date, and active/retired status.

Confirmed empty history means no equipment; missing history means unknown. Use the recorded role, not one claimed in a request.

## Role Policies

| Role | Item | Maximum active quantity | Replacement interval |
|---|---|---:|---|
| Standard employee | Laptop | 1 | 4 years |
| Standard employee | Monitor | 1 | 3 years |
| Manager | Laptop | 1 | 2 years |
| Manager | Monitor | 2 | 3 years |

## Decision Rules

- **Approve:** complete records confirm first-time or additional equipment within allocation. Replacement intervals do not restrict additional permitted units.
- **Replace:** identify the old asset and require its return/retirement. Eligibility begins on its replacement anniversary, inclusive, using the UTC date. February 29 maps to February 28 in non-leap years.
- **Deny:** gaming consoles are explicitly prohibited for both roles. Other unrecognized items require review.
- **Escalate:** unknown employee/role; missing, conflicting, or invalid records; unclear item or replacement intent; early replacement, excess allocation, or another exception.

Approve replacements only when the identified asset is due and the resulting active allocation remains within policy. Never invent missing facts.

A future start date or issue date anywhere in the employee record blocks an automatic decision. Employee lookup reports an invalid record and eligibility escalates before applying item policy.

Persist each escalation with a review ID, supplied employee ID, request, reason, and timestamp, even when employee identity is unverified. Confirm escalation only after storage succeeds. Tool failures must prevent unsupported decisions.

Assign a request ID at intake. The agent retries a timed-out review write once with the original payload. If you rerun the agent for the same request, pass `--request-id` with the original ID. For direct tool calls, run `prepare-review` once and reuse its JSON. A separate request receives its own ID even if the text is identical.

The agent verifies explicit first-issue, additional, and replacement requests before using the model's eligibility arguments. Use phrases such as “Can I get my first work laptop?”, “I need a second monitor”, and “Replace my damaged laptop LAP-002”. A clear gaming-console request can receive the role's policy denial. Requests outside these supported forms go to review.

The reviewer starts a fresh model conversation with the exact draft, original request, policy decision, tool observations, and any saved review receipt. It gives a verdict and reason without calling tools. Only a confirmed draft is released. A request for more evidence can trigger one more investigation. An existing review stays authoritative, and a failed check still returns its receipt.

## Demonstrations and Testing

| Outcome | Example |
|---|---|
| Approve | First work laptop; confirmed empty history |
| Deny | Gaming console request |
| Escalate | Damaged laptop before replacement date |
| Escalate | Missing equipment history |

Follow TDD one unit-test case at a time before MCP registration, including boundaries and data integrity. Verify employee-lookup-only connectivity before adding tools. Final evidence includes four traces/responses, escalation reasons, reflection, and passing CI with tests enabled.

## ReAct evaluation

Run the 26 scripted scenarios from the repo root. The runner gives each attempt its own seeded SQLite database and scores the decision, reason, tool arguments, and review persistence.

```sh
docker compose run --rm -T dev python -m evals.react_eval
```

For a live Foundry run, put the deployment settings in `.env`. The command below uses `reasoning_effort=none` for tool calls on the lab's GPT-6.1 Terra deployment.

```sh
docker compose run --rm -T --env-from-file .env -e FOUNDRY_REASONING_EFFORT=none dev python -m evals.react_eval --live
```

The runner prints one JSON object per attempt and a final summary. Use `--case` to select a scenario and `--repeat-boundaries` to set the repeat count for cases marked for repetition.

## Reflection evaluation

The paired eval uses the saved live ReAct report at `output/react-eval-live.jsonl`. It replays four supported drafts and four intentionally flawed drafts against the reviewer. Only the draft changes within each pair. The report scores flawed drafts released, supported drafts withheld, and median review time.

Run from the repo root with `.env` configured:

```sh
docker compose run --rm -T --env-from-file .env dev python -m evals.reflection_eval > output/reflection-eval-live.jsonl
```

The runner prints one JSON object per review and a final summary. Use `--baseline` to select a saved Part 4 report. The replay makes model calls and uses the saved evidence, so it leaves employee and review records untouched.
