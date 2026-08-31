# CLAUDE.md — Ask My Cohort

Databricks Campus Hackathon (BMSCE), Track A. Team `tech`. 12-hour build, 1 Sep 2026.

## What this is

A Genie agent that lets a faculty advisor ask, in plain English, which of their students
are quietly falling behind — answered over governed campus data on Databricks, with
role-based access enforced in Unity Catalog beneath Genie.

**Genie is the product.** Not a dashboard with a chatbot bolted on. If a change makes the
dashboard better and Genie's answers worse, it is the wrong change.

**One primary user: the faculty advisor.** Students, deans and admins are served by the
same agent, but the advisor is who we design and demo for.

## Rules that never bend

1. **Six-week cutoff.** `date` columns are day-offsets from module start. The filter is
   `WHERE date <= 42` and it is applied **exactly once, in silver**. Never re-derive it
   downstream, never relax it "just for this feature". Training on full-term behaviour
   leaks the outcome: the model scores beautifully and warns nobody in time.
2. **No demographics in any feature set.** `studentInfo` carries gender, age band,
   disability and a deprivation index. None of them enter any model, ever, not even
   as a control. Behaviour only: clicks, submissions, timing, scores.
3. **Binary label.** `final_result` has four values. Collapse: `Fail` + `Withdrawn` =
   at-risk (1), everything else 0. Four-class is more interesting and costs an hour
   we do not have.
4. **Attendance is synthetic and disclosed everywhere.** Generated from a causal story
   (latent ability and motivation drive attendance and submissions, which drive marks,
   plus noise), never independently randomised. Every artifact that shows attendance
   says it is synthetic.
5. **Framing rule.** Always show the cost of missing a session, never the allowance.
   "Missing today moves you to medium and leaves 2 sessions of buffer" — never
   "you can skip 4 more."

## Schema contract — do not rename anything

```
campus.bronze.{student_info, student_vle, student_assessment, assessments,
               student_registration, courses, vle, attendance_synth}

campus.silver.student_week
  student_id, code_module, code_presentation, week,
  clicks, active_days, distinct_resources, submissions,
  avg_score, days_late_avg, sessions_held, sessions_attended, is_at_risk
  -- date <= 42 applied HERE

campus.gold.risk_signals
  student_id, student_name, code_module, code_presentation, advisor_id,
  department, risk_score, risk_band, top_factor_1, top_factor_2,
  top_factor_3, scored_at

campus.gold.session_forecasts
  code_module, code_presentation, session_date, expected_headcount,
  lower_bound, upper_bound, model_version

campus.gold.attendance_buffers
  student_id, student_name, code_module, advisor_id, department,
  sessions_held, sessions_attended, attendance_pct, threshold_pct,
  sessions_remaining, sessions_missable, buffer_band, cost_of_missing_next

campus.ops.role_map
  user_email, role, advisor_id, student_id, department
```

`buffer_band` ∈ {`low`, `medium`, `high`, `unavoidable`}.
`risk_band` ∈ {`low`, `medium`, `high`}.
`role` ∈ {`advisor`, `dean`, `student`, `admin`}.

## Ownership — file-level, no shared edits

| Path | Owner | Everyone else |
|---|---|---|
| `notebooks/01_bronze_ingest.py` … `05_gold.py` | Aditya | read only |
| `notebooks/ml/*` | Aditya | read only |
| `sql/governance/*` | Ojash | read only |
| `sql/genie/*` | Ojash | read only |
| `docs/*`, `deck/*` | Satyam | read only |
| `qa/*` | Kirtee | read only |

Dependency runs one direction: Aditya writes `campus.gold.*`, Ojash governs them.
If a gold column is wrong, message the owner. Do not edit another owner's file.

## Free Edition constraints — rules, not preferences

- Serverless only. No GPUs, no custom clusters.
- Python and SQL only. Scala and R are disabled.
- One Databricks App per account; apps auto-stop after 24 hours.
- Outbound internet restricted. No live scraping, no third-party APIs at runtime.
- Exceeding fair-use quota kills compute for the rest of the day. Four verified
  accounts = four independent budgets. Use them as redundancy.

## Governance is the differentiator

The role hierarchy is not frontend work. It is Unity Catalog row filters and column
masks on one governed set of tables, so the identical English question returns
different answers depending on who asks:

- advisor → their own students, by name
- dean → department aggregates, names masked
- student → their own row only

Use `campus.ops.role_map` joined on `current_user()`, **not**
`is_account_group_member()` — the group function may fail silently on Free Edition
and an empty demo is worse than an unfashionable implementation.

## Deliberately not building — these are stated exclusions, not gaps

- **AI-written-work detection.** Detectors are unreliable and false positives land on
  real students as cheating accusations.
- **Subject preference prediction.** No ground truth exists for whether a student
  enjoys a subject.
- **Per-student next-day absence.** Driven by factors absent from the data.
- **Vector Search / RAG.** There is no document corpus to retrieve over. Naming
  services we do not use is the anti-pattern judges spot fastest.

Do not revisit these without a stated reason. The exclusions read as judgement.

## Code style for this repo

- Notebooks are `.py` files with `# COMMAND ----------` cell separators so they import
  cleanly into Databricks.
- SQL over PySpark wherever both work. It reads faster under time pressure and Genie's
  example questions are SQL anyway.
- Every gold table gets `COMMENT ON TABLE` and `COMMENT ON COLUMN` for every column.
  Genie's answer quality is metadata quality. This is not polish.
- No secrets, no tokens, no API keys in the repo.
- Prefer one long readable cell over five clever abstracted ones. Nobody is maintaining
  this on 2 September.

## Timeboxes

- Data generation: 90 minutes, hard stop. Least valuable hour of the twelve.
- Model not converged by hour 7 → ship logistic regression, no debate.
- Hour 10 is a hard freeze. No new features after it, including "quick" ones.

## Answers the jury will want

- *Is Genie core or a skin?* It is the only interface. No dashboard exists.
- *Your attendance data is fake?* Synthetic, disclosed, derived from real engagement.
  The risk model trains on real OULAD outcomes.
- *What is your accuracy?* We validate the pipeline, not the science. Any number from a
  12-hour build on partly synthetic data would mislead, so we do not quote one.
- *Why six weeks?* Full-term features leak the outcome. A model that needs the whole
  term cannot warn anyone during it.
- *Could flags be discriminatory?* No demographic feature enters the model. By design.
- *Self-fulfilling prophecy?* Flags go to an advisor for intervention, never a ranking,
  never shown to the student as a label.
- *Why Databricks not a notebook?* The hard parts are fragmentation and governance, not
  modelling. Unity Catalog solves multi-role access to one sensitive table declaratively.

## Compliance note

Student records are personal data under India's DPDP Act, 2023. Any real deployment
needs a lawful basis, purpose limitation and data minimisation, with the institution as
data fiduciary. Two design consequences: column masking means most roles never receive
identifying fields at all, and risk flags must never become automated decisions — no
model gates a grade, an enrolment, or a disciplinary action.