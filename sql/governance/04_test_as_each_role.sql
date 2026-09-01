-- Role-switch test sequence: flips ojashkgupta@gmail.com's row in role_map through each
-- of the four roles, selecting from campus.gold.risk_signals after each flip, then
-- restores the original role (advisor) before moving on. attendance_buffers carries the
-- identical rf_risk + mask_name pair, so testing risk_signals here covers both.
--
-- This is the exact role-switch sequence from CLAUDE.md's fallback plan: run live if a
-- teammate can't log in with their own account, to demo all four roles from one login.
--
-- Reminder: permission and mask changes cache. If a result looks wrong, restart the SQL
-- warehouse before assuming the filter/mask logic is broken.

-- === Test as ADVISOR (baseline role for this account) ===
UPDATE campus.ops.role_map SET role = 'advisor'
WHERE lower(user_email) = lower('ojashkgupta@gmail.com');

SELECT student_id, student_name, advisor_id, department, risk_band
FROM campus.gold.risk_signals;
-- expect: only rows where advisor_id = 'ADV001' (own students only), student_name
-- visible, unmasked

UPDATE campus.ops.role_map SET role = 'advisor'
WHERE lower(user_email) = lower('ojashkgupta@gmail.com');
-- restore: already advisor, no-op


-- === Test as DEAN ===
UPDATE campus.ops.role_map SET role = 'dean'
WHERE lower(user_email) = lower('ojashkgupta@gmail.com');

SELECT student_id, student_name, advisor_id, department, risk_band
FROM campus.gold.risk_signals;
-- expect: all rows, across every advisor/department, but student_name = 'REDACTED' on
-- every row (department aggregates, names masked)

UPDATE campus.ops.role_map SET role = 'advisor'
WHERE lower(user_email) = lower('ojashkgupta@gmail.com');
-- restore to advisor


-- === Test as STUDENT ===
-- Sets student_id, not just role: this account's role_map row normally carries only
-- advisor_id (NULL student_id), and rf_risk's student clause is "student_id = stu_id" —
-- NULL never equals anything, so leaving student_id unset silently returns zero rows
-- with no error. '11391' is a real student_id confirmed to exist in
-- campus.gold.risk_signals (unlike the original placeholder 'S100234', which was never
-- validated against real data and returns no rows).
UPDATE campus.ops.role_map SET role = 'student', student_id = '11391'
WHERE lower(user_email) = lower('ojashkgupta@gmail.com');

SELECT student_id, student_name, advisor_id, department, risk_band
FROM campus.gold.risk_signals;
-- expect: rows only where student_id = '11391' (own record only, likely 1 row per
-- code_module/code_presentation the student is enrolled in), student_name visible

UPDATE campus.ops.role_map SET role = 'advisor', student_id = NULL
WHERE lower(user_email) = lower('ojashkgupta@gmail.com');
-- restore to advisor, and clear student_id back to NULL (advisor role doesn't use it)


-- === Test as ADMIN ===
UPDATE campus.ops.role_map SET role = 'admin'
WHERE lower(user_email) = lower('ojashkgupta@gmail.com');

SELECT student_id, student_name, advisor_id, department, risk_band
FROM campus.gold.risk_signals;
-- expect: all rows, every advisor/department, student_name visible and unmasked (full
-- access, used for QA/debugging)

UPDATE campus.ops.role_map SET role = 'advisor'
WHERE lower(user_email) = lower('ojashkgupta@gmail.com');
-- restore to advisor — leave role_map back in its real state
