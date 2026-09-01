# Ask My Cohort

**A Genie agent that answers "which of my students are quietly falling behind?" over
governed campus data — where Unity Catalog, not the interface, decides who may see what.**

Databricks Campus Hackathon (BMSCE) · Track A: Real-World Campus Problem Solver ·
Theme: Genie-Powered Campus Intelligence · Team `tech`

---

## The problem

A faculty advisor cannot answer that question today. It requires joining attendance, LMS
activity and assessment records across three systems they have no query access to. So
nobody asks, and the answer arrives in week fourteen alongside the results.

## The solution

Ask in plain English. Get an answer scoped to who you are.

The same question, asked by different people, returns different data — and that difference
is enforced in the data layer, beneath the agent, so it cannot be bypassed by querying the
tables directly.

| Role | Rows returned | `student_name` |
|---|---|---|
| Advisor | only their own `advisor_id` | visible |
| Dean | every row | `'REDACTED'` |
| Student | only their own `student_id` | visible |
| Admin | every row | visible |
| No `role_map` row | none | `'REDACTED'` |

That table is not a description of intent. It is the observable behaviour of two Unity
Catalog functions in [`sql/governance/01_functions.sql`](sql/governance/01_functions.sql),
attached in [`02_attach.sql`](sql/governance/02_attach.sql):

- `campus.ops.rf_risk(adv_id, stu_id)` — row filter on `risk_signals` and
  `attendance_buffers`
- `campus.ops.mask_name(name)` — column mask on `student_name`

Both resolve the caller with `campus.ops.role_map` joined on `lower(current_user())`,
deliberately rather than `is_account_group_member()`, which can fail silently on Free
Edition.

Note the dean row: a dean is **not** department-scoped. It sees every row and differs from
an admin only by the name mask. The app says so rather than implying otherwise.

## What it answers

Three questions, three gold tables, nothing else attached to the agent:

| Table | Question | How |
|---|---|---|
| `campus.gold.risk_signals` | Who is trending toward failing | Logistic regression, weeks 1–6 behaviour, MLflow-registered |
| `campus.gold.attendance_buffers` | What missing the next session costs | Pure arithmetic. No model. It cannot be wrong |
| `campus.gold.session_forecasts` | Expected headcount next session | Aggregate only, never per-student |

## Architecture

```
OULAD CSVs + generated attendance
        │
   bronze/   7 raw Delta tables, source column names kept, nothing joined
        │
   silver/   one row per student per week
             `date` <= 42 applied exactly once, here
             id_student → student_id, all demographic columns dropped
        │
     gold/   risk_signals · attendance_buffers · session_forecasts
        │       ▲ row filter + column mask attached here
        │
   Genie agent ── the only interface
        │
   web app on Databricks Apps
             forwards each user's own token, so UC enforces per user
```

Currently **32,593 enrolments** across 7 departments, 7 modules and 1,096 advisors.

## Repository layout

```
notebooks/         medallion pipeline, 00 setup → 05 gold
sql/governance/    the row filter, the column mask, role assignments, grants
sql/genie/         table comments, agent instructions, verified example questions
backend/           FastAPI app: /api/me, gold reads, Genie proxy
web/               dependency-free HTML/CSS/JS front end
data/headers/      5-row samples of the seven OULAD files
CLAUDE.md          project rules and schema contract — the source of truth
```

The real OULAD CSVs are never committed: they exceed GitHub's file limit and the dataset is
public, so anyone who needs it downloads it.

## Running it

The pipeline runs as notebooks `00` → `05` on a serverless cluster. The governance SQL runs
in the SQL Editor on a serverless warehouse, in order:

```
01_functions.sql   create rf_risk and mask_name
02_attach.sql      attach them (only after gold exists)
06_grants.sql      catalog/schema/table grants — without these, callers hit
                   INSUFFICIENT_PERMISSIONS before rf_risk is ever evaluated
05_fix_role_map.sql  one account per role
```

`03_rollback.sql` detaches everything. `04_test_as_each_role.sql` walks a single account
through all four roles, which is the honest way to demo the difference from one login —
Unity Catalog genuinely re-enforces on each change.

The web app deploys to Databricks Apps:

```bash
databricks sync backend "/Workspace/Users/<you>/ask-my-cohort-app"
databricks sync web     "/Workspace/Users/<you>/ask-my-cohort-app/web"
databricks apps deploy ask-my-cohort \
  --source-code-path "/Workspace/Users/<you>/ask-my-cohort-app"
```

It needs user authorization with the `sql` and `dashboards.genie` scopes:

```bash
databricks apps create-update ask-my-cohort --json \
  '{ "update_mask": "user_api_scopes",
     "app": { "user_api_scopes": ["sql", "dashboards.genie"] } }'
```

That scope grant is what makes the governance real. Databricks forwards the signed-in
user's own access token in `x-forwarded-access-token`; the backend passes it to both Genie
and the SQL warehouse, so Unity Catalog evaluates policy per user instead of a shared
service account flattening everyone into one identity.

Configuration lives in `backend/app.yaml` and `backend/.env.example`
(`DATABRICKS_HOST`, `DATABRICKS_SQL_WAREHOUSE_ID`, `DATABRICKS_GENIE_SPACE_ID`).
No secrets are committed.

## Stated plainly

- **Attendance is synthetic and disclosed**, generated from a causal story over real
  engagement, and labelled as such on every surface that shows it. The risk model trains on
  real OULAD outcomes.
- **No demographic column enters any feature set.** `gender`, `region`, `imd_band`,
  `age_band`, `highest_education` and `disability` load into bronze because bronze is raw,
  and die at the silver boundary.
- **Six-week cutoff, applied once.** Training on full-term behaviour leaks the outcome: the
  model scores beautifully and warns nobody in time.
- **No accuracy figure is quoted.** A number from a short build on partly synthetic data
  would mislead.
- **Risk flags go to an advisor for intervention** — never into a ranking, never shown to
  the student as a label, never gating a grade or an enrolment. The student view shows
  attendance arithmetic and deliberately shows no risk score.
- **The framing rule:** always the cost of missing the next session, never an allowance to
  spend.

## Deliberately not built

Stated as judgement, not as gaps:

- **AI-written-work detection** — detectors are unreliable, and false positives land on real
  students as cheating accusations.
- **Subject preference prediction** — no ground truth exists.
- **Per-student next-day absence** — driven by factors absent from the data.
- **Vector Search / RAG** — there is no document corpus to retrieve over. Naming services
  you don't use is the anti-pattern judges spot fastest.

## Compliance

Student records are personal data under India's DPDP Act, 2023. A real deployment needs a
lawful basis, purpose limitation and data minimisation, with the institution as data
fiduciary. Two design consequences carried here: column masking means most roles never
receive identifying fields at all, and risk flags must never become automated decisions.
