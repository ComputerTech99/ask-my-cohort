# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Generate Synthetic Attendance
# MAGIC
# MAGIC Writes **`campus.bronze.attendance_synth`** — a causal synthetic
# MAGIC attendance table whose rows are grounded in real OULAD engagement.
# MAGIC
# MAGIC ## How the data is generated (disclose everywhere)
# MAGIC
# MAGIC Two per-student latent variables — **ability** and **motivation** — are
# MAGIC drawn from N(0,1).  Motivation is *partially anchored* to each student's
# MAGIC real first-42-day `sum_click` total from `studentVle.csv`: students who
# MAGIC clicked more in the first six weeks are shifted toward higher motivation.
# MAGIC Attendance probability for every session is then a logistic function of
# MAGIC ability + motivation + session-level noise.
# MAGIC
# MAGIC Because both latents derive (partly) from real behaviour, per-student
# MAGIC attendance rates are genuinely correlated with first-42-day clicks.
# MAGIC The script validates this at the end and **raises loudly** if the
# MAGIC correlation is near zero.
# MAGIC
# MAGIC **This table is SYNTHETIC. Never treat it as measured data.**
# MAGIC
# MAGIC **Owner:** Aditya  |  **Track A — Ask My Cohort (BMSCE Hackathon 2026)**

# COMMAND ----------

import numpy as np
import pandas as pd
from pyspark.sql import functions as F

# Reproducibility seed — fix this so reruns produce the same table.
RNG_SEED = 42

# Path to raw source files in the Unity Catalog volume.
RAW_PATH = "/Volumes/campus/bronze/raw/"

# Roughly how many teaching sessions per module-presentation.
SESSIONS_PER_PRESENTATION = 24

# First module day used as anchor for session dates.
# Sessions are spread across the first 168 days (≈ 24 weeks at 7 days/session).
SESSION_SPACING_DAYS = 7
SESSION_START_DAY = 7   # first session on day 7 to avoid pre-module clutter

# Six-week engagement cutoff — matches the silver rule exactly.
EARLY_WINDOW_DAYS = 42

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 1 — Load studentVle early-window clicks per student per module-presentation

# COMMAND ----------

# studentVle.csv.gz — Spark reads .gz natively.
# Columns confirmed from data/headers/studentVle_head.csv:
#   code_module, code_presentation, id_student, id_site, date, sum_click
# "date" is a reserved-word-adjacent column name; backtick it in SQL.

vle_raw = (
    spark.read
    .option("header", "true")
    .option("inferSchema", "true")
    .option("quote", '"')
    .csv(RAW_PATH + "studentVle.csv.gz")
)

# Aggregate: total clicks per (id_student, code_module, code_presentation)
# restricted to the first 42 days.
early_clicks = (
    vle_raw
    .filter(F.col("`date`") <= EARLY_WINDOW_DAYS)
    .groupBy("id_student", "code_module", "code_presentation")
    .agg(F.sum("sum_click").alias("early_clicks"))
)

print(f"early_clicks rows: {early_clicks.count():,}")
early_clicks.show(5)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 2 — Load distinct (student, module, presentation) enrollments
# MAGIC
# MAGIC We use studentInfo because it is the authoritative enrollment list.
# MAGIC Columns confirmed from data/headers/studentInfo_head.csv:
# MAGIC   code_module, code_presentation, id_student, gender, region,
# MAGIC   highest_education, imd_band, age_band, num_of_prev_attempts,
# MAGIC   studied_credits, disability, final_result

# COMMAND ----------

info_raw = (
    spark.read
    .option("header", "true")
    .option("inferSchema", "true")
    .option("quote", '"')
    .csv(RAW_PATH + "studentInfo.csv")
)

# Keep only the enrollment key; no demographics enter the generator.
enrollments = info_raw.select("id_student", "code_module", "code_presentation").distinct()

print(f"Distinct enrollments: {enrollments.count():,}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 3 — Join clicks onto enrollments; students with zero early clicks get 0

# COMMAND ----------

# Left join so students who never clicked in the early window still appear
# (early_clicks = 0, which is valid information).
enroll_clicks = (
    enrollments
    .join(early_clicks, on=["id_student", "code_module", "code_presentation"], how="left")
    .fillna({"early_clicks": 0})
)

# Collect to pandas for numpy sampling — the full enrollment table is small
# (≈ 32 000 rows) and fits comfortably in driver memory.
df = enroll_clicks.toPandas()
print(f"Enrollment rows collected to driver: {len(df):,}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 4 — Sample latent ability and motivation; anchor motivation to real clicks

# COMMAND ----------

rng = np.random.default_rng(RNG_SEED)

n = len(df)

# Raw latents: independent standard normals.
ability_raw    = rng.standard_normal(n)
motivation_raw = rng.standard_normal(n)

# Normalise early_clicks to z-score within each module-presentation so that
# the anchor effect is on relative engagement, not absolute click counts
# (some modules have far more clickable resources than others).
df["_click_z"] = (
    df.groupby(["code_module", "code_presentation"])["early_clicks"]
    .transform(lambda s: (s - s.mean()) / (s.std() + 1e-9))
)

# Motivation = 0.6 * normalised_clicks + 0.8 * raw_noise
# (mixing weight chosen so Pearson r ≈ 0.4–0.6 with clicks, not 1.0 — leaves
# room for realistic variance in attendance.)
motivation = 0.6 * df["_click_z"].values + 0.8 * motivation_raw

# Ability is fully independent of clicks — models a different construct.
ability = ability_raw

df["_ability"]    = ability
df["_motivation"] = motivation

print("Latent variable summary:")
print(pd.DataFrame({"ability": ability, "motivation": motivation}).describe().round(3))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 5 — Generate session rows with causal attendance

# COMMAND ----------

# Build session schedule: SESSIONS_PER_PRESENTATION evenly spaced sessions
# per (code_module, code_presentation).  The schedule is the same for every
# student in the same presentation — only attendance differs.

# Get distinct module-presentations and assign session dates.
pres_list = df[["code_module", "code_presentation"]].drop_duplicates().values.tolist()

session_rows = []
for mod, pres in pres_list:
    for s in range(SESSIONS_PER_PRESENTATION):
        session_date = SESSION_START_DAY + s * SESSION_SPACING_DAYS
        session_rows.append({
            "code_module": mod,
            "code_presentation": pres,
            "session_no": s + 1,
            "session_date": session_date,
        })

sessions_df = pd.DataFrame(session_rows)
print(f"Session schedule rows: {len(sessions_df):,} "
      f"({len(pres_list):,} presentations × {SESSIONS_PER_PRESENTATION} sessions)")

# Cross-join enrollments with sessions using a merge on module-presentation key.
df_cross = df.merge(sessions_df, on=["code_module", "code_presentation"], how="inner")
df_cross = df_cross.sort_values(
    ["id_student", "code_module", "code_presentation", "session_no"]
).reset_index(drop=True)

print(f"Total attendance rows to generate: {len(df_cross):,}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 6 — Logistic attendance probability with session-level noise

# COMMAND ----------

# Logit = ability + motivation + session_noise
# Intercept of 0.5 centres the population attendance rate near ~62%.
INTERCEPT = 0.5

session_noise = rng.standard_normal(len(df_cross)) * 0.5

logit = (
    INTERCEPT
    + df_cross["_ability"].values
    + df_cross["_motivation"].values
    + session_noise
)

prob_attend = 1.0 / (1.0 + np.exp(-logit))

# Bernoulli draw: attended = 1 if uniform < prob_attend.
attended = (rng.uniform(size=len(df_cross)) < prob_attend).astype(int)

df_cross["attended"] = attended

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 7 — Write to campus.bronze.attendance_synth

# COMMAND ----------

# Select only the contract columns; drop all internal scratch columns.
output_cols = [
    "id_student",
    "code_module",
    "code_presentation",
    "session_no",
    "session_date",
    "attended",
]

attendance_pd = df_cross[output_cols].copy()

# Cast to correct types before converting to Spark.
attendance_pd["id_student"]    = attendance_pd["id_student"].astype(int)
attendance_pd["session_no"]    = attendance_pd["session_no"].astype(int)
attendance_pd["session_date"]  = attendance_pd["session_date"].astype(int)
attendance_pd["attended"]      = attendance_pd["attended"].astype(int)

attendance_spark = spark.createDataFrame(attendance_pd)

(
    attendance_spark
    .write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.bronze.attendance_synth")
)

print("✓ Written campus.bronze.attendance_synth")
spark.sql("DESCRIBE TABLE campus.bronze.attendance_synth").show(truncate=False)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 8 — Table comment (disclosure required by CLAUDE.md rule 4)

# COMMAND ----------

spark.sql("""
    COMMENT ON TABLE campus.bronze.attendance_synth IS
    'SYNTHETIC DATA — not measured attendance.
     Generated on build day (2026-09-01) by notebooks/01_generate_attendance.py.
     Method: two per-student latent variables (ability, motivation) are drawn
     from N(0,1); motivation is partially anchored (weight 0.6) to the
     normalised first-42-day sum_click total from studentVle so that attendance
     has a genuine causal relationship with real engagement behaviour.
     Attendance probability per session = logistic(0.5 + ability + motivation
     + N(0, 0.5) session noise). Seed=42 for reproducibility.
     ~24 sessions per module-presentation. Never treat as ground truth.'
""")

print("✓ Table comment set")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 9 — Validation: mean attendance rate and click-attendance correlation
# MAGIC
# MAGIC **If the correlation is near zero the causal story is broken.**
# MAGIC The script will raise an AssertionError so the problem is impossible to miss.

# COMMAND ----------

# ── Mean attendance rate ───────────────────────────────────────────────────
overall_mean = attended.mean()
print(f"Overall mean attendance rate : {overall_mean:.3f}  (expected ~0.55–0.70)")

# ── Per-student attendance rate ────────────────────────────────────────────
per_student_att = (
    df_cross.groupby("id_student")["attended"].mean().rename("att_rate")
)

# ── Per-student first-42-day clicks ───────────────────────────────────────
# (Collapse across module-presentations: a student registered for multiple
#  modules contributes multiple rows; we sum across all.)
per_student_clicks = (
    df.groupby("id_student")["early_clicks"].sum().rename("total_clicks")
)

# Align on id_student.
val_df = pd.concat([per_student_att, per_student_clicks], axis=1).dropna()

correlation = val_df["att_rate"].corr(val_df["total_clicks"])
print(f"Pearson r(attendance_rate, early_clicks): {correlation:.4f}")

# ── Threshold check ────────────────────────────────────────────────────────
MIN_ACCEPTABLE_CORRELATION = 0.10   # anything below this means the causal
                                     # link to real data is effectively absent

if correlation < MIN_ACCEPTABLE_CORRELATION:
    raise AssertionError(
        f"\n\n"
        f"╔══════════════════════════════════════════════════════════════╗\n"
        f"║  GENERATOR FAILURE: correlation too low!                    ║\n"
        f"║                                                              ║\n"
        f"║  Pearson r(attendance_rate, early_clicks) = {correlation:.4f}       ║\n"
        f"║  Minimum required                         = {MIN_ACCEPTABLE_CORRELATION:.4f}       ║\n"
        f"║                                                              ║\n"
        f"║  The synthetic attendance has no real relationship to       ║\n"
        f"║  student engagement. Check the motivation anchor weight     ║\n"
        f"║  and the click normalisation logic in Step 4.               ║\n"
        f"╚══════════════════════════════════════════════════════════════╝"
    )

print(
    f"\n✓ Correlation check PASSED  (r = {correlation:.4f} >= {MIN_ACCEPTABLE_CORRELATION})\n"
    f"  Synthetic attendance is genuinely correlated with real engagement.\n"
    f"  campus.bronze.attendance_synth is ready."
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Validation summary table

# COMMAND ----------

summary = pd.DataFrame({
    "metric": [
        "total rows written",
        "distinct students",
        "distinct module-presentations",
        "sessions per presentation",
        "mean attendance rate",
        "r(attendance_rate, early_clicks)",
    ],
    "value": [
        f"{len(attendance_pd):,}",
        f"{attendance_pd['id_student'].nunique():,}",
        f"{len(pres_list):,}",
        str(SESSIONS_PER_PRESENTATION),
        f"{overall_mean:.4f}",
        f"{correlation:.4f}",
    ],
})

display(spark.createDataFrame(summary))
