# Databricks notebook source
# MAGIC %md
# MAGIC # 05 — Gold Tables
# MAGIC
# MAGIC Writes the three governed gold tables consumed by Genie and applies
# MAGIC all `COMMENT ON` metadata that drives Genie's answer quality.
# MAGIC
# MAGIC | Table | Source | Notes |
# MAGIC |---|---|---|
# MAGIC | `campus.gold.risk_signals` | `silver.model_a_risk_scores` + generated metadata | risk_band thresholds: ≥ 0.60 high, ≥ 0.35 medium, < 0.35 low |
# MAGIC | `campus.gold.session_forecasts` | `silver.model_b_session_forecasts` | Promoted as-is from model B staging |
# MAGIC | `campus.gold.attendance_buffers` | `bronze.attendance_synth` | Pure arithmetic — no model |
# MAGIC
# MAGIC Also populates `campus.ops.role_map` so governance row-filters work
# MAGIC on first run.
# MAGIC
# MAGIC **Owner:** Aditya  |  **Track A — Ask My Cohort (BMSCE Hackathon 2026)**

# COMMAND ----------

import hashlib
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from pyspark.sql import functions as F
from pyspark.sql.types import LongType, DoubleType, IntegerType, StringType

# ── Risk band thresholds ───────────────────────────────────────────────────
RISK_HIGH_THRESHOLD   = 0.60   # risk_score >= this → "high"
RISK_MEDIUM_THRESHOLD = 0.35   # risk_score >= this and < high → "medium"
                                # risk_score <  0.35            → "low"

# ── Attendance buffer constants ────────────────────────────────────────────
TOTAL_SESSIONS = 24             # sessions per module-presentation (matches 01)
THRESHOLD_PCT  = 75.0           # minimum attendance percentage
MIN_ATTEND     = math.ceil(TOTAL_SESSIONS * THRESHOLD_PCT / 100)   # = 18
ADVISOR_BATCH  = 30             # target students per advisor

# ── Pseudonymous display names ─────────────────────────────────────────────
# Deterministic from student_id via MD5.  OULAD contains no real names;
# these are generated placeholders for demo readability only.
FIRST_NAMES = [
    "Arjun",   "Priya",    "Rahul",    "Sneha",   "Vikram",  "Pooja",
    "Amit",    "Divya",    "Rajesh",   "Sunita",  "Kiran",   "Neha",
    "Suresh",  "Anjali",   "Mahesh",   "Kavya",   "Ramesh",  "Shreya",
    "Sunil",   "Meera",    "Anil",     "Deepa",   "Vinod",   "Nisha",
    "Ashok",   "Rekha",    "Sanjay",   "Geeta",   "Ravi",    "Shalini",
    "Vijay",   "Lalitha",  "Mohan",    "Uma",     "Prakash", "Savitha",
    "Gopal",   "Sudha",    "Shyam",    "Padma",   "Hari",    "Lakshmi",
    "Girish",  "Saritha",  "Anand",    "Bhavana", "Naveen",  "Swathi",
    "Prasad",  "Asha",     "Manoj",    "Vidya",   "Santosh", "Hema",
    "Krishna", "Vani",     "Ganesh",   "Kavitha", "Arun",    "Geetha",
    "Harish",  "Nandini",  "Rajan",    "Sowmya",
]
LAST_NAMES = [
    "Kumar",        "Sharma",       "Reddy",        "Singh",
    "Nair",         "Patel",        "Joshi",        "Rao",
    "Gupta",        "Verma",        "Iyer",         "Menon",
    "Pillai",       "Mehta",        "Shah",         "Malhotra",
    "Bhat",         "Hegde",        "Kulkarni",     "Desai",
    "Pandey",       "Sinha",        "Tiwari",       "Mishra",
    "Agarwal",      "Bansal",       "Garg",         "Jain",
    "Kapoor",       "Khanna",       "Arora",        "Bose",
    "Chatterjee",   "Das",          "Ghosh",        "Mukherjee",
    "Roy",          "Sen",          "Chakraborty",  "Banerjee",
    "Biswas",       "Paul",         "Sarkar",       "Saha",
    "Chaudhary",    "Srivastava",   "Dubey",        "Goyal",
    "Naik",         "Shinde",
]

# ── Department: OULAD module code → campus department ─────────────────────
DEPT_MAP = {
    "AAA": "Computing and Information Technology",
    "BBB": "Business and Management",
    "CCC": "Science and Technology",
    "DDD": "Arts and Humanities",
    "EEE": "Engineering",
    "FFF": "Mathematics and Statistics",
    "GGG": "Social Sciences",
}

print("Constants loaded.")
print(f"  Risk thresholds  : high >= {RISK_HIGH_THRESHOLD}, medium >= {RISK_MEDIUM_THRESHOLD}")
print(f"  Attendance buffer: threshold = {THRESHOLD_PCT}%, minimum attend = {MIN_ATTEND}/{TOTAL_SESSIONS}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Helper Functions

# COMMAND ----------

def student_display_name(student_id: int) -> str:
    """
    Deterministic pseudonymous display name from student_id.
    Uses MD5 bytes as stable indices into fixed name lists.
    These are placeholder names over anonymised OULAD IDs — not real people.
    """
    h = hashlib.md5(str(student_id).encode()).digest()
    return f"{FIRST_NAMES[h[0] % len(FIRST_NAMES)]} {LAST_NAMES[h[1] % len(LAST_NAMES)]}"


def get_department(code_module: str) -> str:
    return DEPT_MAP.get(str(code_module).upper(), "General Studies")


def compute_risk_band(score: float) -> str:
    if score >= RISK_HIGH_THRESHOLD:
        return "high"
    elif score >= RISK_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def compute_buffer_band(sessions_missable: int) -> str:
    """
    Buffer band from sessions_missable (how many more sessions the student
    can miss and still reach the 75% threshold at end of term).
      unavoidable: threshold unreachable even with perfect attendance (sm < 0)
      high        : 0–2 sessions of buffer (very tight)
      medium      : 3–4 sessions of buffer
      low         : 5+ sessions of buffer (comfortable)
    """
    if sessions_missable < 0:
        return "unavoidable"
    elif sessions_missable <= 2:
        return "high"
    elif sessions_missable <= 4:
        return "medium"
    return "low"


def compute_cost_sentence(sessions_missable: int, sessions_remaining: int) -> str:
    """
    Plain-English sentence stating the COST of missing the very next session.
    Per CLAUDE.md framing rule: always state the cost, never the allowance.
    Never write 'you can skip N more'.
    """
    if sessions_missable < 0:
        return (
            f"The 75% attendance threshold is already mathematically unreachable. "
            f"Even attending all {sessions_remaining} remaining sessions is not sufficient "
            f"to recover — intervention is needed immediately."
        )

    if sessions_missable == 0:
        return (
            "Missing the next session makes the 75% attendance threshold "
            "mathematically unreachable — every remaining session must be attended."
        )

    new_sm    = sessions_missable - 1
    cur_band  = compute_buffer_band(sessions_missable)
    new_band  = compute_buffer_band(new_sm)
    s_word    = "session" if new_sm == 1 else "sessions"

    if new_sm < 0:
        return (
            "Missing the next session makes the 75% attendance threshold "
            "mathematically unreachable."
        )

    if new_band != cur_band:
        if new_sm == 0:
            return (
                f"Missing the next session moves you to {new_band} risk — "
                f"every remaining session must then be attended to reach 75%."
            )
        return (
            f"Missing the next session moves you to {new_band} risk, "
            f"leaving {new_sm} {s_word} of buffer before the "
            f"75% threshold becomes unreachable."
        )

    return (
        f"Missing the next session reduces your buffer to {new_sm} {s_word}, "
        f"keeping you in the {cur_band} band."
    )


def assign_advisor_ids(df: pd.DataFrame) -> pd.DataFrame:
    """
    Assign advisor_id deterministically by (code_module, code_presentation).
    Students are sorted by student_id and batched in groups of ADVISOR_BATCH.
    advisor_id format: adv_{module}_{presentation}_{batch_number}
    """
    result = []
    for (mod, pres), grp in df.groupby(["code_module", "code_presentation"]):
        grp_sorted = grp.sort_values("student_id").reset_index(drop=True)
        grp_sorted["advisor_id"] = [
            f"adv_{mod}_{pres}_{i // ADVISOR_BATCH + 1}"
            for i in range(len(grp_sorted))
        ]
        result.append(grp_sorted)
    return pd.concat(result, ignore_index=True)


print("Helper functions defined.")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 1 — Load Staging Tables from Silver

# COMMAND ----------

risk_pd     = spark.table("campus.silver.model_a_risk_scores").toPandas()
forecast_pd = spark.table("campus.silver.model_b_session_forecasts").toPandas()

print(f"model_a_risk_scores       : {len(risk_pd):,} rows")
print(f"model_b_session_forecasts : {len(forecast_pd):,} rows")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 2 — Build Name + Advisor Lookup
# MAGIC
# MAGIC One deterministic row per enrollment
# MAGIC `(student_id, code_module, code_presentation)`.

# COMMAND ----------

enroll = risk_pd[["student_id", "code_module", "code_presentation"]].drop_duplicates()

# Assign advisor IDs — deterministic by module-presentation, ~30 per advisor
enroll = assign_advisor_ids(enroll)

# Add display name and department
enroll["student_name"] = enroll["student_id"].apply(student_display_name)
enroll["department"]   = enroll["code_module"].apply(get_department)

advisor_counts = enroll.groupby(["code_module", "advisor_id"]).size()
print(f"Enrollments         : {len(enroll):,}")
print(f"Unique advisors     : {enroll['advisor_id'].nunique()}")
print(f"Students per advisor (min/mean/max): "
      f"{advisor_counts.min()} / {advisor_counts.mean():.1f} / {advisor_counts.max()}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 3 — campus.gold.risk_signals

# COMMAND ----------

scored_at = datetime.now(timezone.utc)

risk_gold = (
    risk_pd
    .merge(enroll, on=["student_id", "code_module", "code_presentation"], how="left")
)

# risk_band from thresholds stated in CLAUDE.md contract cell above
risk_gold["risk_band"]  = risk_gold["risk_score"].apply(compute_risk_band)
risk_gold["scored_at"]  = scored_at

# Select exactly the gold contract columns
risk_signals_pd = risk_gold[[
    "student_id",
    "student_name",
    "code_module",
    "code_presentation",
    "advisor_id",
    "department",
    "risk_score",
    "risk_band",
    "top_factor_1",
    "top_factor_2",
    "top_factor_3",
    "scored_at",
]].copy()

# ── Write to gold ─────────────────────────────────────────────────────────
risk_spark = (
    spark.createDataFrame(risk_signals_pd)
    .withColumn("student_id", F.col("student_id").cast(LongType()))
    .withColumn("risk_score", F.col("risk_score").cast(DoubleType()))
)

(
    risk_spark.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.gold.risk_signals")
)

print("✓ Written campus.gold.risk_signals")
print(f"  Rows        : {len(risk_signals_pd):,}")
print(f"  Risk bands  :")
print(risk_signals_pd["risk_band"].value_counts().to_string())

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 4 — campus.gold.session_forecasts

# COMMAND ----------

# Promote directly from the Model B staging table.
# Columns already match the gold contract:
#   code_module, code_presentation, session_date,
#   expected_headcount, lower_bound, upper_bound, model_version

(
    spark.createDataFrame(forecast_pd)
    .write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.gold.session_forecasts")
)

print(f"✓ Written campus.gold.session_forecasts  ({len(forecast_pd):,} rows)")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 5 — campus.gold.attendance_buffers (pure arithmetic)
# MAGIC
# MAGIC No model.  Inputs are:
# MAGIC - `bronze.attendance_synth` (actual session attendance)
# MAGIC - The 75% threshold rule
# MAGIC
# MAGIC ### Maths
# MAGIC
# MAGIC ```
# MAGIC sessions_held      = distinct sessions with session_date <= 42
# MAGIC sessions_remaining = TOTAL_SESSIONS - sessions_held   (= 24 - sessions_held)
# MAGIC MIN_ATTEND         = ceil(0.75 × TOTAL_SESSIONS)      (= 18)
# MAGIC need_more          = max(0,  MIN_ATTEND - sessions_attended)
# MAGIC sessions_missable  = sessions_remaining - need_more
# MAGIC ```
# MAGIC
# MAGIC `sessions_missable < 0` → threshold unreachable → `buffer_band = 'unavoidable'`

# COMMAND ----------

att = spark.table("campus.bronze.attendance_synth").toPandas()

# ── Observed sessions (first six weeks, session_date <= 42) ───────────────
att_obs = att[att["session_date"] <= 42]
att_fut = att[att["session_date"] > 42]

obs_stats = (
    att_obs
    .groupby(["id_student", "code_module", "code_presentation"])
    .agg(
        sessions_held     =("session_no", "nunique"),
        sessions_attended =("attended",   "sum"),
    )
    .reset_index()
)

# Sessions remaining per module-presentation (future sessions = sessions 7–24)
pres_remaining = (
    att_fut
    .groupby(["code_module", "code_presentation"])["session_no"]
    .nunique()
    .reset_index()
    .rename(columns={"session_no": "sessions_remaining"})
)

buffers = obs_stats.merge(pres_remaining,
                           on=["code_module", "code_presentation"], how="left")
buffers["sessions_remaining"] = buffers["sessions_remaining"].fillna(0).astype(int)

# ── Buffer arithmetic ─────────────────────────────────────────────────────
buffers["attendance_pct"]   = (
    buffers["sessions_attended"] / buffers["sessions_held"].clip(lower=1) * 100
).round(2)
buffers["threshold_pct"]    = THRESHOLD_PCT
buffers["sessions_missable"] = (
    buffers["sessions_remaining"]
    - (MIN_ATTEND - buffers["sessions_attended"]).clip(lower=0)
).astype(int)

# ── Buffer band and cost sentence ─────────────────────────────────────────
buffers["buffer_band"] = buffers["sessions_missable"].apply(compute_buffer_band)
buffers["cost_of_missing_next"] = buffers.apply(
    lambda r: compute_cost_sentence(r["sessions_missable"], r["sessions_remaining"]),
    axis=1,
)

# ── Add display name, advisor, department via the lookup we already built ──
buffers = buffers.rename(columns={"id_student": "student_id"})
buffers = buffers.merge(
    enroll[["student_id", "code_module", "code_presentation",
            "student_name", "advisor_id", "department"]],
    on=["student_id", "code_module", "code_presentation"],
    how="left",
)

# ── Select contract columns (note: code_presentation added beyond spec
#    minimum for query granularity; it contains no demographic data) ────────
attendance_buffers_pd = buffers[[
    "student_id",
    "student_name",
    "code_module",
    "code_presentation",   # extra — needed for Genie joins; not in spec minimum
    "advisor_id",
    "department",
    "sessions_held",
    "sessions_attended",
    "attendance_pct",
    "threshold_pct",
    "sessions_remaining",
    "sessions_missable",
    "buffer_band",
    "cost_of_missing_next",
]].copy()

(
    spark.createDataFrame(attendance_buffers_pd)
    .withColumn("student_id", F.col("student_id").cast(LongType()))
    .write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.gold.attendance_buffers")
)

print("✓ Written campus.gold.attendance_buffers")
print(f"  Rows         : {len(attendance_buffers_pd):,}")
print(f"  Buffer bands :")
print(attendance_buffers_pd["buffer_band"].value_counts().to_string())

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 6 — campus.ops.role_map
# MAGIC
# MAGIC Governs row-level access: which user sees which rows in gold tables.
# MAGIC **Never attach a row filter to this table** — filter functions cannot
# MAGIC read a table that itself has a filter (CLAUDE.md §Unity Catalog gotchas).

# COMMAND ----------

# ── Student rows ──────────────────────────────────────────────────────────
student_rows = (
    enroll[["student_id", "code_module", "advisor_id", "department"]]
    .drop_duplicates(subset="student_id")          # one row per student globally
    .assign(
        user_email = lambda df: df["student_id"].apply(
            lambda sid: f"student_{sid}@campus.edu"
        ),
        role       = "student",
    )
    [["user_email", "role", "advisor_id", "student_id", "department"]]
)

# ── Advisor rows ──────────────────────────────────────────────────────────
advisor_rows = (
    enroll[["advisor_id", "code_module", "department"]]
    .drop_duplicates(subset="advisor_id")
    .assign(
        user_email = lambda df: df["advisor_id"].apply(
            lambda aid: f"{aid}@campus.edu"
        ),
        role       = "advisor",
        student_id = None,
    )
    [["user_email", "role", "advisor_id", "student_id", "department"]]
)

# ── Dean rows (one per department) ────────────────────────────────────────
dean_rows = pd.DataFrame([
    {
        "user_email": f"dean_{dept.lower().replace(' ', '_').replace('/', '_')}@campus.edu",
        "role":       "dean",
        "advisor_id": None,
        "student_id": None,
        "department": dept,
    }
    for dept in DEPT_MAP.values()
])

# ── Admin row ─────────────────────────────────────────────────────────────
admin_row = pd.DataFrame([{
    "user_email": "admin@campus.edu",
    "role":       "admin",
    "advisor_id": None,
    "student_id": None,
    "department": None,
}])

role_map_pd = pd.concat(
    [student_rows, advisor_rows, dean_rows, admin_row],
    ignore_index=True,
)

# student_id must be Long to match the type in gold tables for filter joins
(
    spark.createDataFrame(role_map_pd)
    .withColumn("student_id",
                F.col("student_id").cast(LongType()))
    .write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.ops.role_map")
)

print(f"✓ Written campus.ops.role_map  ({len(role_map_pd):,} rows)")
print(role_map_pd["role"].value_counts().to_string())

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 7 — COMMENT ON TABLE and COMMENT ON COLUMN
# MAGIC
# MAGIC Genie's answer quality is metadata quality.  Every column gets a
# MAGIC description written for an LLM answering a faculty advisor's question.

# COMMAND ----------

# ── campus.gold.risk_signals ──────────────────────────────────────────────
spark.sql("""
    COMMENT ON TABLE campus.gold.risk_signals IS
    'One row per student per module-presentation showing the predicted risk of
     failing or withdrawing, scored at the end of the six-week engagement window.
     Use this table to answer questions like: which students are most at risk?
     Who should the advisor contact urgently? What is driving a specific student\\'s risk?
     Risk is based on VLE engagement, assessment submissions, scores, attendance rate,
     and engagement trends across weeks 1-6. No demographic data is used.'
""")

col_comments_risk = {
    "student_id":      "Unique numeric identifier for the student. Join to ops.role_map on student_id to apply row-level access control.",
    "student_name":    "Pseudonymous display name generated deterministically from student_id. OULAD data is anonymised; these are placeholder names for demo readability, not real identities.",
    "code_module":     "The OULAD module code this risk score applies to (e.g. AAA, BBB). Answers: which module is the student at risk in?",
    "code_presentation": "The specific presentation (year and semester) of the module, e.g. 2013J. Combined with code_module, uniquely identifies a course run.",
    "advisor_id":      "Identifier of the faculty advisor responsible for this student in this module. Answers: which of my students are high risk? Row filters restrict advisors to their own students.",
    "department":      "Academic department derived from the module code. Answers: which department has the most at-risk students? Used by deans to see department-level aggregates.",
    "risk_score":      "Probability between 0.0 and 1.0 that this student will fail or withdraw before the end of the module. Computed by a logistic regression model trained on six-week engagement features. Higher = more at-risk. Use this for sorting and thresholding: e.g. students with risk_score > 0.6.",
    "risk_band":       "Human-readable risk tier derived from risk_score. Values: high (score >= 0.60), medium (score >= 0.35), low (score < 0.35). Use this for filtering: e.g. show all high-risk students. Answers: which students are in the danger zone?",
    "top_factor_1":    "The single biggest driver of this student\\'s risk score, expressed in plain English for an advisor. Examples: 'low VLE engagement in week 4', 'pattern of late assessment submissions'. Positive means it is increasing risk; the advisor should address this directly.",
    "top_factor_2":    "The second most influential factor in this student\\'s risk score. Same format as top_factor_1. Together with top_factor_1 and top_factor_3, gives the advisor a complete picture of why the risk flag was raised.",
    "top_factor_3":    "The third most influential factor in this student\\'s risk score. When all three factors point to low engagement, the pattern is consistent. When they are mixed, one factor may be an outlier.",
    "scored_at":       "UTC timestamp when the risk model last scored this student. Answers: how recent is this data? All students in a run share the same scored_at value.",
}
for col, comment in col_comments_risk.items():
    spark.sql(f"""
        ALTER TABLE campus.gold.risk_signals
        ALTER COLUMN {col}
        COMMENT '{comment}'
    """)
print("✓ Comments set: campus.gold.risk_signals")

# ── campus.gold.session_forecasts ─────────────────────────────────────────
spark.sql("""
    COMMENT ON TABLE campus.gold.session_forecasts IS
    'Predicted headcount for upcoming teaching sessions in each module-presentation.
     Forecasts are generated from observed attendance in sessions 1-6 using a rolling
     mean plus linear trend model. Use this table to answer: how many students are
     expected at the next session? Is attendance trending up or down? What is the
     expected range for session 20?'
""")

col_comments_forecast = {
    "code_module":        "The OULAD module code. Answers: which module is this session for?",
    "code_presentation":  "The specific presentation of the module. Combined with code_module, identifies a unique course run.",
    "session_date":       "Day offset from the module start date on which the session is scheduled. This is the same scale as the date column in studentVle. Session 7 is typically on day 49, session 8 on day 56, etc.",
    "expected_headcount": "Predicted number of students who will attend this session, based on the rolling mean of the last 3 observed sessions plus a linear trend adjustment. Answers: how many students should I prepare resources for?",
    "lower_bound":        "Lower end of the 95% prediction interval for headcount. If actual attendance falls below this, it is a statistically significant drop worth investigating.",
    "upper_bound":        "Upper end of the 95% prediction interval for headcount. Bounds are computed from the standard deviation of residuals in the observed sessions.",
    "model_version":      "Identifier of the forecasting model version that produced this row. Useful for tracking when forecasts were regenerated.",
}
for col, comment in col_comments_forecast.items():
    spark.sql(f"""
        ALTER TABLE campus.gold.session_forecasts
        ALTER COLUMN {col}
        COMMENT '{comment}'
    """)
print("✓ Comments set: campus.gold.session_forecasts")

# ── campus.gold.attendance_buffers ────────────────────────────────────────
spark.sql("""
    COMMENT ON TABLE campus.gold.attendance_buffers IS
    'Per-student attendance position at the end of the six-week window, with a forward
     projection of how many sessions they can afford to miss before falling below the
     75% attendance threshold. This table supports the framing rule: always show the
     cost of missing a session, never the remaining allowance. Use this table to answer:
     which students are close to losing their attendance standing? What happens if a
     student misses the next session? Who needs immediate intervention?'
""")

col_comments_buffers = {
    "student_id":           "Unique numeric identifier for the student.",
    "student_name":         "Pseudonymous display name. Placeholder over anonymised OULAD IDs — not a real identity.",
    "code_module":          "The module this attendance record applies to.",
    "code_presentation":    "The specific presentation of the module.",
    "advisor_id":           "Faculty advisor responsible for this student in this module.",
    "department":           "Academic department of the module.",
    "sessions_held":        "Number of teaching sessions that have been held in the six-week observation window. Typically 6.",
    "sessions_attended":    "Number of those sessions this student actually attended (from synthetic attendance data).",
    "attendance_pct":       "Current attendance percentage: sessions_attended / sessions_held * 100. Answers: what is this student\\'s attendance rate so far?",
    "threshold_pct":        "The minimum attendance percentage required to pass (75.0). Fixed for all students.",
    "sessions_remaining":   "Number of sessions remaining in the term after the six-week window (typically 18 out of 24 total). This is how much opportunity remains to recover or maintain attendance.",
    "sessions_missable":    "The maximum additional sessions this student can miss and still reach 75% attendance at end of term. Negative means the threshold is already mathematically unreachable. 0 means every remaining session must be attended. Answers: how much room does this student have?",
    "buffer_band":          "Risk tier based on sessions_missable. Values: low (5+ sessions of buffer — comfortable), medium (3-4 sessions), high (0-2 sessions — urgent), unavoidable (threshold already out of reach). Answers: which students are in the danger zone for attendance?",
    "cost_of_missing_next": "A plain-English sentence describing exactly what happens to this student\\'s standing if they miss the very next session. Always phrased as a cost, not an allowance. Example: Missing the next session moves you to high risk, leaving 2 sessions of buffer. Use this as the advisor\\'s talking point for student conversations.",
}
for col, comment in col_comments_buffers.items():
    spark.sql(f"""
        ALTER TABLE campus.gold.attendance_buffers
        ALTER COLUMN {col}
        COMMENT '{comment}'
    """)
print("✓ Comments set: campus.gold.attendance_buffers")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 8 — Validation: Row Counts, Schemas, Demographic Firewall

# COMMAND ----------

GOLD_TABLES = [
    "campus.gold.risk_signals",
    "campus.gold.session_forecasts",
    "campus.gold.attendance_buffers",
]

DEMOGRAPHIC_COLS = {
    "gender", "region", "highest_education",
    "imd_band", "age_band", "disability",
}

print("=" * 60)
print("Gold table validation")
print("=" * 60)

all_clean = True
for tbl in GOLD_TABLES:
    df  = spark.table(tbl)
    n   = df.count()
    cols_lower = {c.name.lower() for c in df.schema}
    leaked = DEMOGRAPHIC_COLS & cols_lower
    if leaked:
        all_clean = False
        print(f"\n✗ {tbl}  — DEMOGRAPHIC LEAK: {leaked}")
    else:
        print(f"\n✓ {tbl}")
        print(f"  Rows    : {n:,}")
        print(f"  Columns : {', '.join(c.name for c in df.schema)}")

print("\n" + "=" * 60)

if not all_clean:
    raise AssertionError(
        "\n\n"
        "╔══════════════════════════════════════════════════════════╗\n"
        "║  DEMOGRAPHIC COLUMN FOUND IN GOLD — SUBMISSION BLOCKED  ║\n"
        "╚══════════════════════════════════════════════════════════╝\n"
        "Fix the SELECT in Step 3, 4, or 5 and rerun."
    )

print("✓ Demographic firewall PASSED — no demographic columns in any gold table.")
print("\nSample from campus.gold.risk_signals (highest-risk 5):")
display(spark.sql("""
    SELECT student_name, code_module, risk_band, risk_score,
           top_factor_1, top_factor_2, scored_at
    FROM campus.gold.risk_signals
    ORDER BY risk_score DESC
    LIMIT 5
"""))

print("\nSample from campus.gold.attendance_buffers (most critical):")
display(spark.sql("""
    SELECT student_name, code_module, sessions_attended, sessions_held,
           attendance_pct, sessions_missable, buffer_band, cost_of_missing_next
    FROM campus.gold.attendance_buffers
    WHERE buffer_band IN ('unavoidable', 'high')
    ORDER BY sessions_missable
    LIMIT 5
"""))
