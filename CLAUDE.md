# CLAUDE.md — Ask My Cohort

Databricks Campus Hackathon (BMSCE), Track A. Team `tech`.
**Build day 1 Sep 2026, 09:00–18:00 — nine hours, not twelve.** Submission portal closes
at 18:00.

Repo: `ask-my-cohort` (private). This file lives at the repo root and is the single
source of truth for names, rules and deadlines. If something here conflicts with a PDF,
a chat message or someone's memory, this file wins.

## What this is

A Genie agent that lets a faculty advisor ask, in plain English, which of their students
are quietly falling behind — answered over governed campus data on Databricks, with
role-based access enforced in Unity Catalog beneath Genie.

**Genie is the product.** Not a dashboard with a chatbot bolted on. If a change makes a
dashboard better and Genie's answers worse, it is the wrong change.

**One primary user: the faculty advisor.** Students, deans and admins are served by the
same agent, but the advisor is who we design and demo for.

## Rules that never bend

1. **Six-week cutoff.** `date` columns are day-offsets from module start. The filter is
   `` WHERE `date` <= 42 `` and it is applied **exactly once, in silver**. Never
   re-derived downstream, never relaxed. Training on full-term behaviour leaks the
   outcome: the model scores beautifully and warns nobody in time.
2. **No demographics in any feature set.** `studentInfo.csv` carries `gender`, `region`,
   `highest_education`, `imd_band`, `age_band`, `disability`. They load into bronze
   because bronze is raw. They die at the silver boundary and never enter a model, not
   even as controls. Behaviour only: clicks, submissions, timing, scores.
3. **Binary label.** `final_result` has four values. Collapse: `Fail` + `Withdrawn` =
   at-risk (1), everything else 0. Four-class costs an hour we do not have.
4. **Attendance is synthetic and disclosed everywhere.** Generated from a causal story
   (latent ability and motivation drive attendance and submissions, which drive marks,
   plus noise), never independently randomised. Every artifact that shows attendance says
   it is synthetic.
5. **Framing rule.** Always show the cost of missing a session, never the allowance.
   "Missing today moves you to medium and leaves 2 sessions of buffer" — never "you can
   skip 4 more."

## The real OULAD schema — verified from the actual files

Headers are committed at `data/headers/*_head.csv`. Read them before writing code. Note
the source CSVs are quote-wrapped; Spark handles this, but it explains any quoted column
name appearing in an error.

| File | Columns |
|---|---|
| `courses.csv` | `code_module`, `code_presentation`, `module_presentation_length` |
| `assessments.csv` | `code_module`, `code_presentation`, `id_assessment`, `assessment_type`, `date`, `weight` |
| `vle.csv` | `id_site`, `code_module`, `code_presentation`, `activity_type`, `week_from`, `week_to` |
| `studentInfo.csv` | `code_module`, `code_presentation`, `id_student`, `gender`, `region`, `highest_education`, `imd_band`, `age_band`, `num_of_prev_attempts`, `studied_credits`, `disability`, `final_result` |
| `studentRegistration.csv` | `code_module`, `code_presentation`, `id_student`, `date_registration`, `date_unregistration` |
| `studentAssessment.csv` | `id_assessment`, `id_student`, `date_submitted`, `is_banked`, `score` |
| `studentVle.csv` | `code_module`, `code_presentation`, `id_student`, `id_site`, `date`, `sum_click` |

### Five traps in this schema

1. **The source says `id_student`. The gold contract says `student_id`.** Bronze keeps
   `id_student` unchanged, because bronze is raw. The rename happens in silver. Renaming
   in bronze breaks the raw rule; forgetting in silver breaks the contract.
2. **`studentAssessment.csv` has no module columns.** Only `id_assessment`. Getting from
   a submission to a module means joining through `assessments.csv` first. A naive join
   here produces wrong numbers silently rather than an error.
3. **`date` is a literal column name** in `studentVle.csv` and `assessments.csv`.
   Backtick it in SQL.
4. **Nulls are load-bearing.** `date_unregistration` is null for students who never
   withdrew — that is information. `score` is null for non-submissions. `imd_band` has
   genuine blanks. Never blanket-fill.
5. **`studentVle.csv` is 453.8 MB**, essentially the whole dataset. A gzipped copy
   (`studentVle.csv.gz`, roughly 80–110 MB) exists locally. Spark reads `.gz` with no
   code change; upload the compressed one.

## Schema contract — do not rename anything

```
campus.bronze.{student_info, student_vle, student_assessment, assessments,
               student_registration, courses, vle, attendance_synth}
  -- bronze keeps source column names, including id_student and the demographics

campus.silver.student_week
  student_id, code_module, code_presentation, week,
  clicks, active_days, distinct_resources, submissions,
  avg_score, days_late_avg, sessions_held, sessions_attended, is_at_risk
  -- `date` <= 42 applied HERE; id_student renamed to student_id HERE;
  -- all six demographic columns dropped HERE

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

`buffer_band` ∈ {`low`, `medium`, `high`, `unavoidable`}
`risk_band` ∈ {`low`, `medium`, `high`}
`role` ∈ {`advisor`, `dean`, `student`, `admin`}

Volume for raw uploads: **`/Volumes/campus/bronze/raw/`**

## Repo layout

```
ask-my-cohort/
├── CLAUDE.md              <- this file, root only, never duplicated into docs/
├── .gitignore             <- *.csv with !data/headers/*.csv, plus *.gz
├── data/headers/          <- 5-row samples of all seven OULAD files (committed)
├── notebooks/             <- Aditya
├── sql/governance/        <- Ojash
├── sql/genie/             <- Ojash
├── qa/                    <- Kirtee
└── docs/                  <- build day plan, pipeline prompt
```

The real OULAD CSVs are **never** committed. They exceed GitHub's 100 MB limit and the
dataset is public — anyone who needs it downloads it.

## Ownership — file-level, no shared edits

| Path | Owner | Everyone else |
|---|---|---|
| `notebooks/*` | Aditya | read only |
| `sql/governance/*`, `sql/genie/*` | Ojash | read only |
| `docs/*` | Satyam | read only |
| `qa/*` | Kirtee | read only |

Dependency runs one direction: Aditya writes `campus.gold.*`, Ojash governs them. If a
gold column is wrong, message the owner. Nobody opens another owner's file.

## The nine-hour clock

| Time | What must be true |
|---|---|
| 09:30 | Catalog, schemas, `role_map` exist. **Schema contract frozen — no renames after this.** |
| 10:30 | Attendance generator done. Hard stop, 60 minutes not 90. |
| 11:00 | Genie space started, written against this contract before gold exists. |
| 13:00 | **Demographic check.** Zero demographic columns in any feature list. |
| 13:30 | Model decision point. Not converged → logistic regression ships, no debate. |
| 14:30 | Deck frozen. |
| 15:00 | **Gold tables populated.** Aditya messages the group. |
| 16:00 | Row filters and masks applied and tested per role. Role screenshots captured. |
| **16:30** | **HARD FREEZE.** Nothing new starts. Satyam calls it out loud. |
| 17:00 | Submitted — an hour before the portal closes. |
| 18:00 | Portal closes. |

## What was cut to fit nine hours

- **The Databricks App / any UI.** Cancelled outright. Genie is the product and the
  submission already states no dashboard exists.
- **The second model attempt.** Logistic regression is the shipped model.
- **30 minutes of data generation.** 60 minutes, not 90.

Not cut, and will not be: the governance layer, the Genie space curation, the freeze.
Those three are the submission. If the build falls behind, the risk model gets simpler —
never the filters, never the comments, never the rehearsal.

## Governance is the differentiator

One governed set of tables with row filters and column masks, so the identical English
question returns different answers depending on who asks:

- advisor → their own students, by name
- dean → department aggregates, names masked
- student → their own row only

Use `campus.ops.role_map` joined on `current_user()`, **not**
`is_account_group_member()` — the group function may fail silently on Free Edition and an
empty demo is worse than an unfashionable implementation.

### Unity Catalog gotchas, learned the hard way

- Run all governance DDL in the **SQL Editor on a serverless warehouse**, not a notebook.
- Column types passed to a filter or mask function **must match the function's parameter
  types exactly**. A mismatch produces an error that does not mention types.
- A filter function **cannot read a table that itself has a filter or mask**. So
  `ops.role_map` stays unprotected. Never attach a filter to it.
- `current_user()` returns the login email and **case mismatches silently return zero
  rows**. Wrap every comparison in `lower()`.
- Permission and policy changes **cache**. Wrong rows for a role → restart the SQL
  warehouse before debugging anything else.

### Genie facts that matter

- Genie Spaces are now called **Genie Agents** in the UI.
- Genie evaluates data access using **each end user's own Unity Catalog permissions**.
  This is the sentence that makes the architecture correct — say it to the judges.
- Roughly **20 questions per minute per workspace**, shared across all spaces. Ojash and
  Kirtee will throttle each other unless they agree slots.
- Attach only the three gold tables. Every extra table is a way for Genie to generate the
  wrong join on stage.

## Free Edition constraints — rules, not preferences

- Serverless only. No GPUs, no custom clusters.
- Python and SQL only. Scala and R disabled.
- One workspace and one metastore per account, with admin restrictions.
- Exceeding fair-use quota kills compute for the rest of the day. Data and settings
  survive; the day does not. Four verified accounts are four independent budgets — they
  are backup, not extra parallelism to burn.
- Outbound internet restricted. Nothing calls an external API at runtime.

## Deliberately not building — stated exclusions, not gaps

- **AI-written-work detection.** Detectors are unreliable and false positives land on
  real students as cheating accusations.
- **Subject preference prediction.** No ground truth exists.
- **Per-student next-day absence.** Driven by factors absent from the data.
- **Vector Search / RAG.** No document corpus to retrieve over. Naming services we do not
  use is the anti-pattern judges spot fastest.

Do not revisit without a stated reason. The exclusions read as judgement.

## Code style

- Notebooks are `.py` with `# COMMAND ----------` cell separators so they import cleanly.
- SQL over PySpark wherever both work — faster to read under pressure, and Genie's
  example questions are SQL anyway.
- Every gold table gets `COMMENT ON TABLE` and `COMMENT ON COLUMN` for every column.
  Genie's answer quality is metadata quality. This is not polish.
- No secrets, tokens or keys in the repo.
- One long readable cell beats five clever abstracted ones. Nobody maintains this on
  2 September.

## Answers the jury will want

- *Is Genie core or a skin?* It is the only interface. No dashboard exists.
- *Your attendance data is fake?* Synthetic, disclosed, derived from real engagement. The
  risk model trains on real OULAD outcomes.
- *What is your accuracy?* We validate the pipeline, not the science. Any number from a
  nine-hour build on partly synthetic data would mislead, so we do not quote one.
- *Why six weeks?* Full-term features leak the outcome. A model that needs the whole term
  cannot warn anyone during it.
- *Could flags be discriminatory?* No demographic feature enters the model. By design.
- *Self-fulfilling prophecy?* Flags go to an advisor for intervention, never a ranking,
  never shown to the student as a label.
- *Why Databricks not a notebook?* The hard parts are fragmentation and governance, not
  modelling. Unity Catalog solves multi-role access to one sensitive table declaratively.

## Compliance note

Student records are personal data under India's DPDP Act, 2023. Real deployment needs a
lawful basis, purpose limitation and data minimisation, with the institution as data
fiduciary. Two design consequences: column masking means most roles never receive
identifying fields at all, and risk flags must never become automated decisions — no
model gates a grade, an enrolment, or a disciplinary action.