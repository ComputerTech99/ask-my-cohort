-- =============================================================================
-- sql/genie/comments.sql
-- Genie metadata: COMMENT ON TABLE and COMMENT ON COLUMN for all three
-- campus.gold tables.
--
-- Written for a reader that is an LLM answering a faculty advisor's question.
-- Each description explains what the column MEANS and what kinds of advisor
-- questions it answers — not its data type.
--
-- Owner: Ojash (edits from 11:00 on build day)
-- Run in: Databricks SQL Editor on a serverless warehouse.
-- One statement per block — safe to re-run individually.
-- =============================================================================


-- ============================================================================
-- campus.gold.risk_signals
-- ============================================================================

COMMENT ON TABLE campus.gold.risk_signals IS
'One row per student per module-presentation showing the predicted risk of
 failing or withdrawing, scored after the first six weeks of engagement.
 Powered by a logistic regression model trained on VLE clicks, assessment
 submissions, scores, attendance rate, and engagement trends. No demographic
 data enters the model. Use this table to answer: which students are most at
 risk? Who should the advisor contact urgently? What is driving a specific
 student''s risk flag? Row-level security restricts each advisor to their own
 students; deans see department aggregates; students see only their own row.';

COMMENT ON COLUMN campus.gold.risk_signals.student_id IS
'Unique numeric identifier for the student. Matches student_id in
 campus.gold.attendance_buffers and campus.ops.role_map. Row-level access
 control is applied via ops.role_map joined on this column.';

COMMENT ON COLUMN campus.gold.risk_signals.student_name IS
'Pseudonymous display name generated deterministically from student_id.
 The underlying OULAD dataset is fully anonymised and contains no real names.
 These names are generated placeholders so that the demo reads like a real
 cohort. Do not treat them as real identities. Example: "Arjun Kumar".';

COMMENT ON COLUMN campus.gold.risk_signals.code_module IS
'OULAD module code identifying the subject (e.g. AAA, BBB, CCC). Answers:
 which module is this risk score for? Which module has the most at-risk
 students? Use with code_presentation to identify a unique course run.';

COMMENT ON COLUMN campus.gold.risk_signals.code_presentation IS
'The specific presentation (year and semester) of the module, e.g. 2013J.
 Combined with code_module, uniquely identifies one run of a course. A student
 may appear more than once if they are enrolled in multiple presentations.';

COMMENT ON COLUMN campus.gold.risk_signals.advisor_id IS
'Identifier of the faculty advisor responsible for this student in this
 module-presentation. Roughly 20-40 students are assigned per advisor.
 Row filters restrict each advisor to rows where their advisor_id matches.
 Answers: which of my students are high risk? Show me my at-risk cohort.';

COMMENT ON COLUMN campus.gold.risk_signals.department IS
'Academic department of the module, derived from code_module. Used by deans
 to view department-level risk summaries without seeing individual student
 names (names are masked for the dean role). Answers: how many high-risk
 students does the Computing department have this term?';

COMMENT ON COLUMN campus.gold.risk_signals.risk_score IS
'Predicted probability (0.0 to 1.0) that this student will fail or withdraw
 before the end of the module. Computed by a logistic regression model from
 six-week engagement features. Higher is worse. Use for sorting and
 thresholding: e.g. "show students with risk_score > 0.6" or "who has the
 highest risk in module BBB?" Do not quote this number as an accuracy
 guarantee — it is a signal to trigger advisor outreach, not an automated
 decision.';

COMMENT ON COLUMN campus.gold.risk_signals.risk_band IS
'Human-readable risk tier derived from risk_score. Three values:
   high   (risk_score >= 0.60) — immediate advisor attention recommended
   medium (risk_score >= 0.35) — monitor closely, consider outreach
   low    (risk_score <  0.35) — no immediate concern
 Use this column for natural-language filtering: "show me all high-risk
 students" or "how many students are in the medium band in module AAA?"';

COMMENT ON COLUMN campus.gold.risk_signals.top_factor_1 IS
'The single biggest driver of this student''s risk score, written in plain
 English for a faculty advisor. Examples: "low VLE engagement in week 4",
 "pattern of late assessment submissions", "declining engagement trend across
 weeks 1-6". A positive contribution means this factor is pushing the student
 toward at-risk. Use this column when an advisor asks: why is this student
 flagged? What should I focus on in my conversation with them?';

COMMENT ON COLUMN campus.gold.risk_signals.top_factor_2 IS
'The second most influential factor in this student''s risk score. Same format
 as top_factor_1. When top_factor_1 and top_factor_2 both point to low
 engagement, the pattern is consistent and the advisor message is clear.
 When they point in opposite directions, one factor may be an outlier.';

COMMENT ON COLUMN campus.gold.risk_signals.top_factor_3 IS
'The third most influential factor. Together, top_factor_1 through
 top_factor_3 give the advisor a complete and actionable explanation of the
 risk score without requiring them to interpret model coefficients.';

COMMENT ON COLUMN campus.gold.risk_signals.scored_at IS
'UTC timestamp when the risk model last scored this student. All students
 scored in one pipeline run share the same scored_at value. Answers: when
 was this data last updated? Is this score from today?';


-- ============================================================================
-- campus.gold.session_forecasts
-- ============================================================================

COMMENT ON TABLE campus.gold.session_forecasts IS
'Predicted student headcount for upcoming teaching sessions in each
 module-presentation. Forecasts are generated from observed attendance in
 sessions 1-6 (the six-week window) using a rolling mean of the last three
 sessions plus a linear trend correction. Confidence bounds come from the
 standard deviation of residuals. Use this table to answer: how many students
 are expected at the next session? Is attendance trending up or down this
 term? What is the expected range for session 15?';

COMMENT ON COLUMN campus.gold.session_forecasts.code_module IS
'Module code for which this headcount forecast applies. Answers: what is
 the expected turnout for module CCC sessions?';

COMMENT ON COLUMN campus.gold.session_forecasts.code_presentation IS
'Specific presentation of the module. Combined with code_module, identifies
 the unique course run this forecast belongs to.';

COMMENT ON COLUMN campus.gold.session_forecasts.session_date IS
'Scheduled day of the session as a day-offset from module start (same scale
 as the date column in studentVle). Session 7 is typically day 49, session 8
 is day 56, and so on at 7-day intervals. Answers: what is the forecast for
 the session on day 77?';

COMMENT ON COLUMN campus.gold.session_forecasts.expected_headcount IS
'Predicted number of students who will attend this session. Computed as:
 rolling mean of the last 3 observed headcounts plus a linear trend
 adjustment of slope * steps_ahead. Answers: how many students should the
 lecturer prepare resources for? Is attendance expected to grow or shrink?';

COMMENT ON COLUMN campus.gold.session_forecasts.lower_bound IS
'Lower end of the 95% prediction interval for headcount (expected_headcount
 minus 1.96 standard deviations of residuals). If actual attendance falls
 below this value it is a statistically significant drop worth flagging.
 Answers: what is the worst realistic case for attendance?';

COMMENT ON COLUMN campus.gold.session_forecasts.upper_bound IS
'Upper end of the 95% prediction interval. Useful for room capacity planning
 and resource allocation in the optimistic scenario.';

COMMENT ON COLUMN campus.gold.session_forecasts.model_version IS
'Label identifying which version of the forecasting model produced this row.
 Useful when forecasts are regenerated mid-term to track changes. Current
 value: rolling_mean_trend_v1.';


-- ============================================================================
-- campus.gold.attendance_buffers
-- ============================================================================

COMMENT ON TABLE campus.gold.attendance_buffers IS
'Per-student attendance position at the end of the six-week engagement window,
 with a forward projection of exactly how many future sessions can be missed
 before falling below the 75% attendance threshold.

 This table supports the campus framing rule: always show the cost of missing
 a session, never the remaining allowance. The cost_of_missing_next column
 gives the advisor a ready-made sentence for student conversations.

 Use this table to answer: which students are close to an attendance crisis?
 What happens to this student if they miss next Tuesday''s session? Who needs
 urgent intervention to stay above 75%? Which module has the worst attendance
 standing overall?

 Attendance data is SYNTHETIC — generated from a causal model anchored to
 real OULAD engagement behaviour. It is disclosed as synthetic in the table
 comment of bronze.attendance_synth.';

COMMENT ON COLUMN campus.gold.attendance_buffers.student_id IS
'Unique numeric identifier for the student. Matches student_id in
 campus.gold.risk_signals. Joining these two tables gives both risk and
 attendance context for a student in a single query.';

COMMENT ON COLUMN campus.gold.attendance_buffers.student_name IS
'Pseudonymous display name. Placeholder over anonymised OULAD IDs — not a
 real identity. Generated deterministically from student_id.';

COMMENT ON COLUMN campus.gold.attendance_buffers.code_module IS
'Module code this attendance record applies to. Answers: which of this
 student''s modules has a problem? Which module has the most students in the
 unavoidable band?';

COMMENT ON COLUMN campus.gold.attendance_buffers.code_presentation IS
'Specific presentation of the module. Added for query granularity — allows
 advisors to distinguish a student''s attendance across different year-groups
 of the same module.';

COMMENT ON COLUMN campus.gold.attendance_buffers.advisor_id IS
'Faculty advisor responsible for this student in this module. Row filters
 restrict each advisor to see only their own students.';

COMMENT ON COLUMN campus.gold.attendance_buffers.department IS
'Academic department of the module. Used for department-level aggregations
 visible to deans.';

COMMENT ON COLUMN campus.gold.attendance_buffers.sessions_held IS
'Number of teaching sessions that have been scheduled and held in the
 six-week observation window (typically 6). This is the denominator for the
 current attendance percentage.';

COMMENT ON COLUMN campus.gold.attendance_buffers.sessions_attended IS
'Number of sessions this student actually attended in the six-week window.
 Together with sessions_held, gives the attendance record so far.
 Answers: how many sessions has this student attended? How many did they miss?';

COMMENT ON COLUMN campus.gold.attendance_buffers.attendance_pct IS
'Current attendance percentage: sessions_attended / sessions_held * 100.
 This is the in-window rate, not the projected end-of-term rate. A student
 at 50% now may still reach 75% by term end if they attend every remaining
 session — see sessions_missable for the forward projection. Answers: what
 is this student''s attendance rate so far?';

COMMENT ON COLUMN campus.gold.attendance_buffers.threshold_pct IS
'The minimum attendance percentage required to maintain good standing (75.0
 for all students in this dataset). Fixed campus policy — not per-student.';

COMMENT ON COLUMN campus.gold.attendance_buffers.sessions_remaining IS
'Number of teaching sessions remaining in the term after the six-week window
 (typically 18 out of 24 total). This is the opportunity remaining to recover
 or maintain attendance standing. Answers: how many sessions are left in the
 term?';

COMMENT ON COLUMN campus.gold.attendance_buffers.sessions_missable IS
'The maximum number of additional sessions this student can miss and still
 reach 75% attendance by end of term. Computed as:
   sessions_remaining - max(0, ceil(0.75 * 24) - sessions_attended)
 Negative value means the 75% threshold is already mathematically unreachable
 regardless of future attendance. Zero means every remaining session must be
 attended. Answers: how much room does this student have? Is it already too
 late?';

COMMENT ON COLUMN campus.gold.attendance_buffers.buffer_band IS
'Risk tier based on sessions_missable. Four values:
   low         (5+ sessions missable)  — comfortable position
   medium      (3-4 sessions missable) — monitor
   high        (0-2 sessions missable) — urgent advisor action recommended
   unavoidable (threshold already out of reach) — immediate intervention
 Answers: which students need urgent attendance intervention? How many
 students are in the unavoidable band in module EEE?';

COMMENT ON COLUMN campus.gold.attendance_buffers.cost_of_missing_next IS
'A ready-to-use plain-English sentence for the advisor describing exactly what
 happens to this student''s standing if they miss the very next session.
 Always phrased as a cost, never as remaining allowance. Examples:
   "Missing the next session moves you to high risk, leaving 2 sessions of
    buffer before the 75% threshold becomes unreachable."
   "Missing the next session makes the 75% threshold mathematically
    unreachable — every remaining session must be attended."
 This column is the advisor''s conversation starter. Genie can return it
 verbatim in response to: what should I tell this student?';
