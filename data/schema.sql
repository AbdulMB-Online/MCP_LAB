PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS employees (
    employee_id TEXT NOT NULL PRIMARY KEY,
    role TEXT NOT NULL,
    start_date TEXT NOT NULL, -- ISO date: YYYY-MM-DD
    -- Only a complete history makes zero asset rows a confirmed empty history.
    equipment_history_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (equipment_history_status IN ('unknown', 'complete'))
);

CREATE TABLE IF NOT EXISTS equipment_assets (
    asset_id TEXT NOT NULL PRIMARY KEY,
    employee_id TEXT NOT NULL REFERENCES employees (employee_id),
    item TEXT NOT NULL,
    issued_on TEXT NOT NULL, -- ISO date: YYYY-MM-DD
    status TEXT NOT NULL CHECK (status IN ('active', 'retired'))
);

CREATE INDEX IF NOT EXISTS equipment_assets_by_employee
    ON equipment_assets (employee_id);

CREATE TABLE IF NOT EXISTS equipment_policies (
    role TEXT NOT NULL,
    item TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('allowed', 'prohibited')),
    max_active_quantity INTEGER,
    replacement_interval_years INTEGER,
    PRIMARY KEY (role, item),
    CHECK (
        (
            disposition = 'allowed'
            AND typeof(max_active_quantity) = 'integer' AND max_active_quantity > 0
            AND typeof(replacement_interval_years) = 'integer'
            AND replacement_interval_years > 0
        ) OR (
            disposition = 'prohibited'
            AND max_active_quantity IS NULL AND replacement_interval_years IS NULL
        )
    )
);

CREATE TABLE IF NOT EXISTS equipment_requests (
    request_id TEXT NOT NULL PRIMARY KEY,
    -- Submitted identifiers can be unknown and still require review.
    employee_id TEXT NOT NULL,
    request_text TEXT NOT NULL CHECK (length(trim(request_text)) > 0),
    requested_item TEXT,
    business_reason TEXT,
    request_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK (request_kind IN ('unknown', 'first_issue', 'additional', 'replacement')),
    replacement_asset_id TEXT,
    requested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS request_decisions (
    request_id TEXT NOT NULL PRIMARY KEY REFERENCES equipment_requests (request_id),
    outcome TEXT NOT NULL CHECK (outcome IN ('approved', 'denied', 'escalated')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    review_id TEXT UNIQUE,
    CHECK (
        (outcome = 'escalated' AND review_id IS NOT NULL AND length(trim(review_id)) > 0)
        OR (outcome IN ('approved', 'denied') AND review_id IS NULL)
    )
);

-- A review and its escalation are stored by the same write.
CREATE VIEW IF NOT EXISTS human_reviews AS
SELECT
    decision.review_id,
    request.request_id,
    request.employee_id,
    request.request_text,
    decision.reason,
    decision.decided_at AS created_at
FROM request_decisions AS decision
JOIN equipment_requests AS request ON request.request_id = decision.request_id
WHERE decision.outcome = 'escalated';

COMMIT;
