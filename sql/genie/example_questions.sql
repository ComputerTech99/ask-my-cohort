-- Example questions for the Genie Agent. Every query below runs against campus.gold.*
-- only. None of them filter by advisor_id or student_id in the SQL text — the row
-- filter (campus.ops.rf_risk) already restricts every result set to what the calling
-- user is allowed to see, before the query runs. This is the detail everyone forgets:
-- if you go looking for a WHERE advisor_id = ... clause and don't find one, that is
-- correct, not a bug.

-- ============================================================================
-- 1. Which of my students are at risk this term?
-- ============================================================================
-- No advisor filter needed or wanted here: rf_risk already restricts rows to the
-- caller's own students before risk_band is even evaluated. Adding an advisor_id
-- predicate would be redundant at best, wrong at worst if the literal doesn't match
-- the caller's actual advisor_id.
SELECT student_id, student_name, code_module, code_presentation, risk_band, risk_score
FROM campus.gold.risk_signals
WHERE risk_band = 'high'
ORDER BY risk_score DESC;

-- ============================================================================
-- 2. Show me the top three factors behind each high-risk student
-- ============================================================================
SELECT student_id, student_name, code_module, code_presentation,
       top_factor_1, top_factor_2, top_factor_3
FROM campus.gold.risk_signals
WHERE risk_band = 'high'
ORDER BY student_id;

-- ============================================================================
-- 3. Which students have the least attendance buffer left?
-- ============================================================================
-- Ordered so the students with the fewest missable sessions come first — this is
-- the cost-of-missing framing, not "who still has the most room to skip."
SELECT student_id, student_name, code_module, buffer_band,
       sessions_missable, cost_of_missing_next
FROM campus.gold.attendance_buffers
ORDER BY sessions_missable ASC;

-- ============================================================================
-- 4. How many students are expected at the next session for a given module?
-- ============================================================================
-- session_forecasts is aggregate, not per-student, so it carries no row filter or
-- mask — this query returns the same result for every role. "Next" means the
-- earliest forecast row on or after today for the module Genie was asked about.
SELECT code_module, code_presentation, session_date,
       expected_headcount, lower_bound, upper_bound
FROM campus.gold.session_forecasts
WHERE code_module = 'BBB' AND code_presentation = '2013J'
  AND session_date >= current_date()
ORDER BY session_date ASC
LIMIT 1;

-- ============================================================================
-- 5. Which students are high risk AND in the unavoidable attendance band?
-- ============================================================================
-- Joined on student_id + code_module + code_presentation, not just module: a
-- student can have separate attendance_buffers rows for different presentations
-- of the same module (retakes), so dropping code_presentation from the join
-- would fan out into duplicate, wrong rows.
SELECT r.student_id, r.student_name, r.code_module, r.code_presentation,
       r.risk_band, b.buffer_band, b.cost_of_missing_next
FROM campus.gold.risk_signals r
JOIN campus.gold.attendance_buffers b
  ON r.student_id = b.student_id
  AND r.code_module = b.code_module
  AND r.code_presentation = b.code_presentation
WHERE r.risk_band = 'high' AND b.buffer_band = 'unavoidable'
ORDER BY r.student_id;

-- ============================================================================
-- 6. How many at-risk students are there per department?
-- ============================================================================
-- THIS ONE is the governance moment: this is one query with no role logic in the
-- SQL itself — no CASE on current_user(), no role branch. The row filter and column
-- mask do all the work underneath it.
--   - An advisor's rf_risk already limits every row to their own students, so what
--     comes back reads as a short named list (their own department only) — real
--     student_name values, because advisor is an unmasked role.
--   - A dean's rf_risk lets every row through, so at_risk_count_in_department is a
--     true cross-department aggregate — but student_name is 'REDACTED' on every row
--     via mask_name, so what a dean actually reads is department totals, not names.
-- Same SQL text, two different answers, purely from who is asking.
SELECT department, student_id, student_name, risk_band,
       COUNT(*) OVER (PARTITION BY department) AS at_risk_count_in_department
FROM campus.gold.risk_signals
WHERE risk_band = 'high'
ORDER BY department, student_id;
