-- Paste this whole block if the demo needs a clean, unfiltered, unmasked table fast.
-- Only run against the two protected gold tables; session_forecasts was never attached
-- (see 02_attach.sql) so there is nothing to drop there.

ALTER TABLE campus.gold.risk_signals DROP ROW FILTER;
ALTER TABLE campus.gold.risk_signals ALTER COLUMN student_name DROP MASK;

ALTER TABLE campus.gold.attendance_buffers DROP ROW FILTER;
ALTER TABLE campus.gold.attendance_buffers ALTER COLUMN student_name DROP MASK;
