-- Grant four guest accounts one role each, for a live per-role demo.
-- Run in the SQL Editor on a serverless warehouse.
--
-- PREREQUISITE, and the part that actually takes the time: each email must first
-- exist as a workspace user. Unity Catalog resolves grants against account-level
-- principals, and the app itself is SSO-gated, so an unknown email cannot even reach
-- the login. Create them first (admin, Databricks CLI):
--
--   databricks users create --json '{"userName":"<email>", "active": true}'
--
-- Object privileges need no further action: 06_grants.sql granted SELECT/USE to
-- `account users`, and the SQL warehouse and Genie space are granted to `users`, so a
-- newly created account inherits everything. Only the role_map row below is per-person.
--
-- Scope columns are not optional. rf_risk matches an advisor on advisor_id and a
-- student on student_id; NULL never equals anything, so a role row missing its column
-- returns zero rows with no error and looks like a broken app.

-- 1. ADVISOR — ADV001 owns a 30-student cohort with 5 flagged high risk. Small enough
--    to read on stage, and it is the only advisor_id with data.
INSERT INTO campus.ops.role_map (user_email, role, advisor_id, student_id, department)
VALUES ('JUDGE_ADVISOR@example.com', 'advisor', 'ADV001', NULL, 'Computing and Information Technology');

-- 2. DEAN — sees every row institution-wide, student_name returns 'REDACTED'.
--    department is display-only; rf_risk does not read it for a dean.
INSERT INTO campus.ops.role_map (user_email, role, advisor_id, student_id, department)
VALUES ('JUDGE_DEAN@example.com', 'dean', NULL, NULL, 'Computing and Information Technology');

-- 3. STUDENT — 28400 (Sneha Reddy) is the strongest demo: 6 sessions held, 1 attended,
--    buffer_band 'high', exactly 1 session of margin left. It exercises the
--    cost-of-missing framing rather than showing a comfortable 100% attendance record.
INSERT INTO campus.ops.role_map (user_email, role, advisor_id, student_id, department)
VALUES ('JUDGE_STUDENT@example.com', 'student', NULL, '28400', 'Computing and Information Technology');

-- 4. ADMIN — every row, names intact, plus the governance console.
INSERT INTO campus.ops.role_map (user_email, role, advisor_id, student_id, department)
VALUES ('JUDGE_ADMIN@example.com', 'admin', NULL, NULL, 'Computing and Information Technology');

-- Verify before the demo: each row must carry the column its role needs.
SELECT user_email, role, advisor_id, student_id
FROM campus.ops.role_map
WHERE user_email IN (
  'JUDGE_ADVISOR@example.com', 'JUDGE_DEAN@example.com',
  'JUDGE_STUDENT@example.com', 'JUDGE_ADMIN@example.com'
);

-- Undo afterwards:
-- DELETE FROM campus.ops.role_map WHERE user_email IN (...same four...);
