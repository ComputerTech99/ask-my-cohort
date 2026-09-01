-- Row filter and column mask functions for campus.gold.*
-- Run in the SQL Editor on a serverless warehouse (per CLAUDE.md Unity Catalog gotchas).
-- CREATE OR REPLACE throughout: idempotent, safe to run more than once today.

-- Row filter: admin/dean see all rows; advisor sees only their own advisor_id; student
-- sees only their own student_id. Uses role_map + current_user() instead of
-- is_account_group_member() because the group function can fail silently on Free
-- Edition, and an empty demo is worse than an unfashionable implementation.
CREATE OR REPLACE FUNCTION campus.ops.rf_risk(adv_id STRING, stu_id STRING)
RETURNS BOOLEAN
RETURN
  EXISTS (
    SELECT 1 FROM campus.ops.role_map rm
    WHERE lower(rm.user_email) = lower(current_user())
      AND rm.role IN ('admin', 'dean')
  )
  OR EXISTS (
    SELECT 1 FROM campus.ops.role_map rm
    WHERE lower(rm.user_email) = lower(current_user())
      AND rm.role = 'advisor'
      AND rm.advisor_id = adv_id
  )
  OR EXISTS (
    SELECT 1 FROM campus.ops.role_map rm
    WHERE lower(rm.user_email) = lower(current_user())
      AND rm.role = 'student'
      AND rm.student_id = stu_id
  );

-- Column mask: advisor/admin/student see the real name; dean sees department
-- aggregates only, so names come back 'REDACTED' — same role_map + current_user()
-- pattern as rf_risk, for the same Free Edition reason.
CREATE OR REPLACE FUNCTION campus.ops.mask_name(name STRING)
RETURNS STRING
RETURN
  CASE
    WHEN EXISTS (
      SELECT 1 FROM campus.ops.role_map rm
      WHERE lower(rm.user_email) = lower(current_user())
        AND rm.role IN ('advisor', 'admin', 'student')
    ) THEN name
    ELSE 'REDACTED'
  END;
