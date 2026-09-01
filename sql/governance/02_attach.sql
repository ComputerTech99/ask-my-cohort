-- Run this only after Aditya confirms gold tables exist and match the schema contract.
-- Running it earlier will fail with a table-not-found error, which is expected and not
-- a bug.

-- risk_signals: per-student rows, so both the row filter and the name mask apply.
ALTER TABLE campus.gold.risk_signals
  SET ROW FILTER campus.ops.rf_risk ON (advisor_id, student_id);

ALTER TABLE campus.gold.risk_signals
  ALTER COLUMN student_name SET MASK campus.ops.mask_name;

-- attendance_buffers: same shape, same rule.
ALTER TABLE campus.gold.attendance_buffers
  SET ROW FILTER campus.ops.rf_risk ON (advisor_id, student_id);

ALTER TABLE campus.gold.attendance_buffers
  ALTER COLUMN student_name SET MASK campus.ops.mask_name;

-- campus.gold.session_forecasts gets NO row filter and NO mask. It is aggregate
-- (per code_module/code_presentation/session_date), not per-student, so there is no
-- identifying row or name column to protect — do not "fix" this later by accident.
