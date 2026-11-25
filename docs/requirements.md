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

Persist each escalation with a review ID, supplied employee ID, request, reason, and timestamp, even when employee identity is unverified. Confirm escalation only after storage succeeds. Tool failures must prevent unsupported decisions.

## Demonstrations and Testing

| Outcome | Example |
|---|---|
| Approve | First work laptop; confirmed empty history |
| Deny | Gaming console request |
| Escalate | Damaged laptop before replacement date |
| Escalate | Missing equipment history |

Follow TDD one unit-test case at a time before MCP registration, including boundaries and data integrity. Verify employee-lookup-only connectivity before adding tools. Final evidence includes four traces/responses, escalation reasons, reflection, and passing CI with tests enabled.
