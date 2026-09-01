-- Role assignments for the live demo, one account per role.
-- Run in the SQL Editor on a serverless warehouse, same as the rest of this folder.
-- Already applied to the workspace; kept here so the mapping is reviewable and repeatable.
--
-- Background trap: rf_risk matches an advisor on `rm.advisor_id = adv_id` and a student on
-- `rm.student_id = stu_id`. NULL never equals anything, so a role row with the wrong column
-- left NULL returns zero rows with no error — it looks like a broken app rather than a
-- misconfigured account. Same trap already documented in 04_test_as_each_role.sql.
-- Every UPDATE below therefore sets the column its role actually needs.

-- admin: sees every row, names visible.
UPDATE campus.ops.role_map
SET role = 'admin'
WHERE lower(user_email) = lower('ojashkgupta@gmail.com');

-- advisor: only rows where advisor_id matches. Every row currently in gold belongs to
-- ADV001, so an advisor mapped to anything else would correctly see nothing at all.
UPDATE campus.ops.role_map
SET role = 'advisor', advisor_id = 'ADV001', student_id = NULL
WHERE lower(user_email) = lower('dsawithaditya@gmail.com');

-- dean: sees every row, student_name comes back 'REDACTED'.
-- department is NOT read by rf_risk — a dean is not department-scoped. It is display only.
UPDATE campus.ops.role_map
SET role = 'dean', department = 'Computing and Information Technology'
WHERE lower(user_email) = lower('kirteejain1802@gmail.com');

-- student: only their own row. 11391 is a real student in gold (Sowmya Jain).
UPDATE campus.ops.role_map
SET role = 'student', student_id = '11391'
WHERE lower(user_email) = lower('satya2025tnb@gmail.com');

-- Verify: one account per role, each carrying the column its role needs.
SELECT user_email, role, advisor_id, student_id, department
FROM campus.ops.role_map
WHERE user_email NOT LIKE '%@campus.edu'
ORDER BY role, user_email;
