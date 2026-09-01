# Genie space setup — order of operations

Attach only the three gold tables — `campus.gold.risk_signals`,
`campus.gold.attendance_buffers`, `campus.gold.session_forecasts` — nothing else, since
every extra table is a way for Genie to generate the wrong join on stage. Run
`comments.sql`'s `COMMENT ON` statements first so column metadata exists before Genie
ever sees the tables, paste `instructions.md` into the Genie Agent's instructions field
second, then add the six questions from `example_questions.sql` as example questions
third. Only after all three are in place, ask each of the six questions live against the
running agent and confirm the answer before treating this space as done.
