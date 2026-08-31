# Claude Code prompt 1 — the pipeline and models

For Aditya. Run in Claude Code from the root of `ask-my-cohort`, where `CLAUDE.md`
already sits. Four separate turns, not one.

---

## Status — what is already done

- Repo `ask-my-cohort` is live and pushed. `CLAUDE.md` is at the root.
- `data/headers/*_head.csv` exists and is committed — five rows of each of the seven
  OULAD files, with the real column names. Claude Code reads these instead of guessing.
- `studentVle.csv.gz` has been generated locally (453.8 MB → roughly 80–110 MB).
- `.gitignore` blocks `*.csv` except the headers, and blocks `*.gz`. The real data never
  enters the repo.

## What still has to happen before Turn 2 runs against anything real

Upload the seven files to a Databricks Volume. In the workspace:
Catalog → `campus` → `bronze` → Create → Volume, named `raw`. That gives
**`/Volumes/campus/bronze/raw/`**, which is hardcoded in the prompts below.

Upload the **gzipped** `studentVle.csv.gz`, not the raw CSV. Spark reads it with no code
change and the upload is four times faster. The other six files are small enough to
upload as-is.

Do this before 09:30 tomorrow. It is the only thing on the critical path that cannot be
parallelised.

---

# Turn 1 — scaffold and the attendance generator

```
Read CLAUDE.md at the repo root first. It contains the schema contract, the real OULAD
column names, and the rules. Follow it exactly and do not rename anything in the
contract.

Also read the files in data/headers/ before writing any code. Those are real five-row
samples of the actual OULAD files and they carry the true column names. Use what you
find there. Do not guess column names and do not rely on your memory of OULAD.

Three things about the source schema that will otherwise cost hours:
- The source column is id_student. The gold contract uses student_id. Bronze keeps
  id_student unchanged because bronze is raw; the rename happens in silver.
- studentAssessment.csv has no code_module or code_presentation, only id_assessment.
  Reaching a module from a submission requires joining through assessments.csv first.
- "date" is a literal column name in studentVle.csv and assessments.csv. Backtick it.
- The CSVs are quote-wrapped. Spark handles this; just be aware of it.

The seven files are at /Volumes/campus/bronze/raw/ with these names:
  assessments.csv  courses.csv  studentAssessment.csv  studentInfo.csv
  studentRegistration.csv  studentVle.csv.gz  vle.csv

Create Databricks notebook files (.py, using `# COMMAND ----------` cell separators so
they import cleanly into a workspace) at:

  notebooks/00_setup.py
  notebooks/01_generate_attendance.py
  notebooks/02_bronze_ingest.py
  notebooks/03_silver.py
  notebooks/04_models.py
  notebooks/05_gold.py

The directories notebooks/, sql/governance/, sql/genie/, qa/ and docs/ already exist. Do
not create a .gitignore, one is already committed.

Write only 00_setup.py and 01_generate_attendance.py in this turn.

00_setup.py creates catalog `campus` and schemas bronze, silver, gold, ops with
CREATE ... IF NOT EXISTS, then prints current_user() and lists the catalog to confirm.

01_generate_attendance.py generates the synthetic attendance table:

- CAUSAL, not randomised. Model two latent per-student variables, ability and motivation,
  drawn from a normal distribution. Derive those latents partly from each student's real
  total sum_click in studentVle where `date` <= 42, so the synthetic column has a genuine
  relationship with real behaviour. Attendance probability per session is a logistic
  function of the latents plus noise. Independently randomised columns give the model
  nothing to learn and we will lose hours debugging code that is not broken.
- Output campus.bronze.attendance_synth with columns:
  id_student, code_module, code_presentation, session_no, session_date, attended (0/1).
  Use id_student here to stay consistent with the other bronze tables.
- Roughly 24 sessions per module presentation.
- COMMENT ON TABLE stating plainly that the data is synthetic and how it was generated.
- Print a validation summary: mean attendance rate, and the correlation between
  per-student attendance rate and per-student first-42-day clicks. If that correlation is
  near zero the generator is wrong — make the script say so loudly rather than passing
  silently.

Working code, not TODOs. PySpark for joins, numpy/pandas for sampling. Readable cells, no
helper modules.

This has a 60-minute budget on build day. Keep it simple.
```

---

# Turn 2 — bronze and silver

```
Now write notebooks/02_bronze_ingest.py and notebooks/03_silver.py, using the real column
names from data/headers/.

02_bronze_ingest.py:
- RAW_PATH = "/Volumes/campus/bronze/raw/" as a variable in the first cell.
- Read each of the seven CSVs, write one Delta table per file into campus.bronze using
  the table names in CLAUDE.md.
- No cleaning, no filtering, no renaming. Bronze is raw by definition, which means the six
  demographic columns in studentInfo DO get loaded here. They get dropped at the silver
  boundary, not this one.
- studentVle arrives gzipped as studentVle.csv.gz. Spark reads gzipped CSV natively. Write
  the read as a glob so it also works if the file is uncompressed or split into parts,
  without a code change.
- Print row counts for all eight bronze tables. Expected order of magnitude: studentVle
  roughly 10 million rows, studentInfo roughly 32 thousand.

03_silver.py builds campus.silver.student_week, exactly one row per
id_student + code_module + code_presentation + week, with id_student renamed to
student_id in the output.

Apply `WHERE `date` <= 42` HERE, exactly once, before any aggregation. Put a loud comment
above it saying this is the leakage cutoff and must not be re-derived or relaxed
downstream. week = floor(`date` / 7) + 1, giving weeks 1 through 6.

Columns per CLAUDE.md: clicks (sum of sum_click), active_days (distinct dates with
activity), distinct_resources (distinct id_site), submissions, avg_score, days_late_avg,
sessions_held, sessions_attended, is_at_risk.

For assessment features: studentAssessment has no module columns, so join it to
assessments on id_assessment first, then to the student. days_late_avg is date_submitted
minus the assessment's `date`, averaged. Both can be null — handle that explicitly rather
than filling with zero, because a missing submission and an on-time submission are not the
same thing.

is_at_risk = 1 when studentInfo.final_result is 'Fail' or 'Withdrawn', else 0.

Do NOT carry gender, region, highest_education, imd_band, age_band or disability into
silver. Not as columns, not as passthroughs. End the notebook with an assertion cell that
fails if any of those six names appears in the silver schema.

Print the final row count and the class balance of is_at_risk.
```

---

# Turn 3 — the two models

```
Now write notebooks/04_models.py. Two models, both logged to MLflow.

Model A, risk classifier:
- Pivot campus.silver.student_week to one row per student with weekly behaviour features
  for weeks 1-6: clicks per week, active_days per week, submissions, avg_score,
  days_late_avg, attendance rate. Plus two trend features: slope of clicks across the six
  weeks, and whether week 6 clicks are below week 1.
- Target is_at_risk, binary.
- sklearn LogisticRegression, class_weight='balanced'. Log it to MLflow FIRST as a working
  baseline. Per CLAUDE.md this is the model we ship — do not attempt anything else unless
  explicitly asked later.
- Log params, ROC-AUC, precision, recall, and the feature list itself as an artifact.
- Assertion cell before training that fails loudly if any feature name matches gender,
  region, age, disability, imd, or education. This runs at 13:00 on build day and it is a
  hard gate.
- Compute per-student top three contributing factors from signed coefficient times
  standardised feature value. Output them as human-readable strings such as "low
  engagement in weeks 4-6", not raw feature names — Genie reads these to an advisor.

Model B, session headcount forecaster:
- Aggregate, not per-student. Expected headcount per code_module + code_presentation for
  an upcoming session, from prior session attendance.
- Rolling mean of the last k sessions plus a linear trend term. Do not reach for a time
  series library.
- lower_bound and upper_bound from the residual standard deviation.
- Log to MLflow.

Both models must run in under ten minutes total on serverless.
```

---

# Turn 4 — gold tables and Genie comments

```
Now write notebooks/05_gold.py, producing the three gold tables exactly as specified in
CLAUDE.md. All three use student_id, not id_student.

campus.gold.risk_signals — one row per student per module, with risk_score, risk_band
(high / medium / low; state your thresholds), and the three top-factor strings from model
A. OULAD is anonymised and has no names, so generate stable pseudonymous display names
deterministically from student_id (a fixed name list plus a hash) so the demo reads like a
real cohort, and COMMENT the column clearly stating these are generated placeholders over
anonymised IDs. Assign advisor_id deterministically by module at roughly 20-40 students
per advisor; derive department from the module code.

campus.gold.session_forecasts — from model B.

campus.gold.attendance_buffers — PURE ARITHMETIC, no model. With threshold_pct = 75:
  sessions_missable = floor of the future sessions a student can miss and still finish at
  or above threshold.
  buffer_band: low if missable >= 5, medium if 3-4, high if 1-2, unavoidable if the
  threshold is already mathematically unreachable.
  cost_of_missing_next: a sentence describing what missing the very next session does,
  phrased as a COST, never an allowance. "Missing the next session drops you to the medium
  band with 3 sessions of margin left." Never "you can still skip N."

Then write every COMMENT ON TABLE and COMMENT ON COLUMN statement for all three gold
tables into sql/genie/comments.sql. Write them for a reader who is an LLM answering a
faculty advisor's question — describe what each column MEANS and what kinds of questions
it answers, not its data type. This file is the single biggest lever on Genie's answer
quality, and Ojash will be editing it from 11:00, so keep it clean with one statement per
block.

End 05_gold.py with a validation cell printing row count and schema for all three gold
tables, asserting no demographic column is present anywhere.
```

---

# Practical notes

Claude Code cannot execute against the Databricks workspace — it writes files that get
imported. Expect the first run of each notebook to fail on a path or a column name. That
is normal.

Import route: Workspace → target folder → Import → File, or connect `ask-my-cohort` as a
Databricks Git folder. Decide which before 09:30 and tell the group.

**Against the nine-hour clock:** generate Turns 1 and 2 the night before so build day
starts with debugging rather than an empty repo. Turns 3 and 4 wait, since they depend on
what silver actually looks like.

**Gold tables are due at 15:00.** Everything downstream of them — the row filters, the
column masks, the Genie example questions, the role screenshots — is blocked until they
exist. If something is going to slip, say so at 13:00, not 15:00.
