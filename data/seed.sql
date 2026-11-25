-- Apply once to a new lab database after schema.sql. All records are synthetic.
PRAGMA foreign_keys = ON;

BEGIN;

INSERT INTO employees (employee_id, role, start_date, equipment_history_status)
VALUES
    ('EMP-001', 'standard', '2020-01-01', 'complete'),
    ('EMP-002', 'standard', '2022-11-25', 'complete'),
    ('EMP-003', 'manager', '2021-03-15', 'unknown'),
    ('EMP-004', 'manager', '2020-06-01', 'complete');

INSERT INTO equipment_assets (asset_id, employee_id, item, issued_on, status)
VALUES
    ('LAP-002', 'EMP-002', 'laptop', '2024-01-15', 'active'),
    ('MON-004-OLD', 'EMP-004', 'monitor', '2021-04-01', 'retired'),
    ('MON-004', 'EMP-004', 'monitor', '2025-04-01', 'active');

INSERT INTO equipment_policies (
    role, item, disposition, max_active_quantity, replacement_interval_years
)
VALUES
    ('standard', 'laptop', 'allowed', 1, 4),
    ('standard', 'monitor', 'allowed', 1, 3),
    ('manager', 'laptop', 'allowed', 1, 2),
    ('manager', 'monitor', 'allowed', 2, 3),
    ('standard', 'gaming_console', 'prohibited', NULL, NULL),
    ('manager', 'gaming_console', 'prohibited', NULL, NULL);

COMMIT;
