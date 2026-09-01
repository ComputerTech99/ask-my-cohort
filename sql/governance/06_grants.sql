-- Object privileges. Run in the SQL Editor on a serverless warehouse.
--
-- WHY THIS FILE EXISTS
-- Row filters and column masks decide WHICH ROWS a user sees. They do not grant access
-- to the objects in the first place. Without these grants a teammate hits a hard error
-- before rf_risk is ever evaluated:
--     [INSUFFICIENT_PERMISSIONS] User does not have USE CATALOG on Catalog 'campus'
-- The catalog was created with no grants at all, so only its owner could read anything.
--
-- WHY `account users` AND NOT INDIVIDUAL EMAILS
-- Granting to a single user fails on this workspace with PRINCIPAL_DOES_NOT_EXIST —
-- Unity Catalog resolves grants against account-level principals, and these accounts are
-- workspace users. `account users` and the `Techy` group both resolve.
--
-- Granting SELECT this broadly is the intended architecture, not a shortcut: one governed
-- set of tables that everyone can query, with rf_risk deciding what each role actually
-- gets back. An advisor still sees only their own students; a dean still gets 'REDACTED'
-- names. Access is scoped by the policy, not by hiding the table.

GRANT USE CATALOG ON CATALOG campus TO `account users`;

GRANT USE SCHEMA ON SCHEMA campus.gold TO `account users`;
GRANT USE SCHEMA ON SCHEMA campus.ops  TO `account users`;

GRANT SELECT ON TABLE campus.gold.risk_signals       TO `account users`;
GRANT SELECT ON TABLE campus.gold.attendance_buffers TO `account users`;
GRANT SELECT ON TABLE campus.gold.session_forecasts  TO `account users`;

-- role_map must stay readable: rf_risk reads it, and the app resolves the caller's role
-- from it. It is also why role_map itself can never carry a filter or mask.
GRANT SELECT ON TABLE campus.ops.role_map TO `account users`;

GRANT EXECUTE ON FUNCTION campus.ops.rf_risk    TO `account users`;
GRANT EXECUTE ON FUNCTION campus.ops.mask_name  TO `account users`;

-- Verify
SHOW GRANTS ON CATALOG campus;
SHOW GRANTS ON TABLE campus.gold.risk_signals;

-- STILL REQUIRED OUTSIDE THIS FILE (not SQL-grantable):
--   1. SQL warehouse — the `users` group already has CAN_USE on the serverless warehouse,
--      so no action needed. Confirm under SQL Warehouses > Permissions if statements fail.
--   2. Genie space — as of writing only the owner and `admins` can use it, so the Ask box
--      fails for everyone else. Grant CAN_RUN to the `users` group in the Genie Agent's
--      Share dialog, or via the permissions API on /api/2.0/permissions/genie/<space_id>.
