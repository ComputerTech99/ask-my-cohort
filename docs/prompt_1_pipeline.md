# Claude Code prompt 1 — the pipeline and models

Give this to Aditya. He runs it in Claude Code in the repo, with `CLAUDE.md` already
committed at the root so Claude reads the rules and schema contract automatically.

Have him run it in **four separate turns**, not one. A single 12-hour build's worth of
code generated in one shot is a debugging session, not a head start.

---

## Turn 1 — scaffold and the synthetic attendance generator

```
Read CLAUDE.md first and follow the schema contract exactly. Do not rename any table
or column.

Create this repo structure with Databricks notebook files (.py, using
`# COMMAND ----------` cell separators so they import cleanly into a Databricks
workspace):

  notebooks/00_setup.py
  notebooks/01_generate_attendance.py
  notebooks/02_bronze_ingest.py
  notebooks/03_silver.py
  notebooks/04_models.py
  notebooks/05_gold.py
  sql/governance/   (empty, owned by Ojash)
  qa/               (empty, owned by Kirtee)
  README.md

Now write notebooks/00_setup.py and notebooks/01_generate_attendance.py only.

00_setup.py creates the catalog `campus` and schemas bronze, silver, gold, ops using
CREATE ... IF NOT EXISTS, and prints the current_user() and the catalog list so we can
confirm it worked.

01_generate_attendance.py generates the synthetic attendance table. Requirements:

- It must be CAUSAL, not randomised. Model two latent per-student variables, ability
  and motivation, drawn from a normal distribution. Attendance probability per session
  is a logistic function of those latents plus noise. Submission behaviour and scores
  in the real OULAD data should CORRELATE with the generated attendance — derive the
  latents partly from each student's real total VLE clicks in the first 42 days, so the
  synthetic column has a genuine relationship with real behaviour. Independently
  randomised columns give the model nothing to learn and we will waste hours debugging
  code that is not broken.
- Output: campus.bronze.attendance_synth with columns
  student_id, code_module, code_presentation, session_no, session_date,
  attended (0/1).
- Roughly 24 sessions per module presentation.
- Add a `COMMENT ON TABLE` that states plainly that this data is synthetic and how it
  was generated.
- Print a validation summary at the end: mean attendance rate, and the correlation
  between per-student attendance rate and per-student first-42-day clicks. If that
  correlation is near zero, the generator is wrong — make the script say so loudly.

Write actual working code, not TODOs. Use PySpark for the joins and numpy/pandas for
the sampling. Keep each cell readable; do not abstract into helper modules.
```

## Turn 2 — bronze and silver

```
Now write notebooks/02_bronze_ingest.py and notebooks/03_silver.py.

02_bronze_ingest.py reads the seven OULAD CSVs from a workspace volume path (make the
path a variable at the top of the notebook, defaulted to
/Volumes/campus/bronze/raw/) and writes one Delta table per file into campus.bronze,
using the exact table names in CLAUDE.md. No cleaning, no filtering, no renaming of
source columns. Print row counts for each table at the end.

studentVle is by far the largest file (~460MB of the 464MB total). Write the read so
that it works if the file has been split into multiple parts — accept a glob path.

03_silver.py builds campus.silver.student_week, exactly one row per
student_id + code_module + code_presentation + week.

Critical: apply `WHERE date <= 42` HERE, exactly once, before any aggregation. Add a
loud comment above it explaining that this is the leakage cutoff and that it must not
be re-derived or relaxed anywhere downstream. Week = floor(date / 7) + 1, so weeks 1
through 6.

Columns per CLAUDE.md: clicks (sum of sum_click), active_days (count of distinct
dates with activity), distinct_resources (count distinct id_site), submissions (count
of assessments submitted in that week), avg_score, days_late_avg (submission date
minus assessment due date, averaged), sessions_held and sessions_attended from the
synthetic attendance table, and is_at_risk.

is_at_risk: 1 when studentInfo.final_result is 'Fail' or 'Withdrawn', else 0.

Do NOT carry gender, age_band, disability, highest_education, imd_band, or region into
silver at all. Not as columns, not as passthroughs. Add an explicit assertion cell at
the end that fails the notebook if any of those column names appear in the silver
schema.

Print the final row count and the class balance of is_at_risk.
```

## Turn 3 — the two models

```
Now write notebooks/04_models.py. Two models, both logged to MLflow.

Model A — risk classifier.
- Features: pivot campus.silver.student_week to one row per student with weekly
  behaviour features for weeks 1-6 (clicks per week, active_days per week, submissions,
  avg_score, days_late_avg, attendance rate). Plus simple trend features: slope of
  clicks across the six weeks, and whether week 6 clicks are below week 1.
- Target: is_at_risk, binary.
- Start with sklearn LogisticRegression with class_weight='balanced'. Log it to MLflow
  first, as a working baseline, BEFORE trying anything better. Only then try
  GradientBoosting and log that as a second run. If the baseline is the only thing
  that works we ship it.
- Log: params, ROC-AUC, precision, recall, and the feature list itself as an artifact.
- Add an assertion cell before training that fails loudly if any feature name matches
  gender, age, disability, imd, region, or education. This is a hard requirement, not
  a nicety.
- Also compute per-student top three contributing factors, using the signed
  coefficient times the standardised feature value for logistic regression. Output them
  as human-readable strings like "low engagement in weeks 4-6" rather than raw feature
  names — Genie will read these out loud to an advisor.

Model B — session headcount forecaster.
- Aggregate, not per-student. Predict expected headcount for an upcoming session per
  code_module + code_presentation, from the attendance history of prior sessions.
- A simple approach is correct here: rolling mean of the last k sessions plus a linear
  trend term. Do not reach for a time series library.
- Produce lower_bound and upper_bound from the residual standard deviation.
- Log to MLflow.

Keep both models fast. Total runtime under ten minutes on serverless.
```

## Turn 4 — gold tables and comments

```
Now write notebooks/05_gold.py, producing the three gold tables exactly as specified
in CLAUDE.md.

campus.gold.risk_signals — one row per student per module, with risk_score,
risk_band (high / medium / low from tertiles or fixed thresholds — state which),
and the three top-factor strings from model A. student_name: OULAD is anonymised and
has no names, so generate stable pseudonymous display names deterministically from
student_id (a fixed name list plus a hash) so the demo reads like a real cohort, and
COMMENT the column clearly stating the names are generated placeholders over
anonymised IDs. advisor_id: assign students to advisors deterministically by module,
roughly 20-40 students per advisor, and department from the module code.

campus.gold.session_forecasts — from model B.

campus.gold.attendance_buffers — PURE ARITHMETIC, no model. Given sessions_held,
sessions_attended, sessions_remaining and a threshold_pct of 75:
  sessions_missable = floor of the number of future sessions the student can miss and
  still end at or above threshold.
  buffer_band: low if missable >= 5, medium if 3-4, high if 1-2,
  unavoidable if the threshold is already mathematically unreachable.
  cost_of_missing_next: a sentence describing what missing the very next session does,
  phrased as a COST and never as an allowance. For example: "Missing the next session
  drops you to the medium band with 3 sessions of margin left." Never phrase it as
  "you can still skip N."

Then write every COMMENT ON TABLE and COMMENT ON COLUMN statement for all three gold
tables into sql/genie/comments.sql as a separate file. Write them for a reader who is
an LLM answering a faculty advisor's question — describe what the column MEANS and
what kinds of questions it answers, not just its data type. This file is the single
biggest lever on Genie's answer quality.

Finally, add a validation cell at the end of 05_gold.py that prints the row count and
schema of all three gold tables and asserts that no demographic column is present.
```

---

**A note for whoever runs this:** Claude Code cannot execute against your Databricks
workspace, so it is writing files you then import. Expect the first run of each
notebook to fail on a column name or a path. That is normal and it is why Turn 1
happens at hour 1 and not hour 5.
