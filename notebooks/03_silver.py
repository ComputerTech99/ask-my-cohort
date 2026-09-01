# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Silver: student_week Feature Table
# MAGIC
# MAGIC Builds **`campus.silver.student_week`** — one row per
# MAGIC `(student_id, code_module, code_presentation, week)`.
# MAGIC
# MAGIC ## What this notebook does
# MAGIC
# MAGIC | Step | What |
# MAGIC |---|---|
# MAGIC | 1 | Apply `date <= 42` leakage cutoff to VLE data (exactly once, here) |
# MAGIC | 2 | Compute week = FLOOR(date / 7) + 1 → weeks 1–6 (spine 1–6) |
# MAGIC | 3 | Aggregate VLE features per student-week |
# MAGIC | 4 | Join studentAssessment → assessments for module context, aggregate per student-week |
# MAGIC | 5 | Compute sessions_held / sessions_attended from attendance_synth |
# MAGIC | 6 | Derive is_at_risk from final_result |
# MAGIC | 7 | Rename id_student → student_id; drop all six demographic columns |
# MAGIC | 8 | Assert no demographic columns leaked into silver |
# MAGIC
# MAGIC **Owner:** Aditya  |  **Track A — Ask My Cohort (BMSCE Hackathon 2026)**

# COMMAND ----------

from pyspark.sql import functions as F

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 1 — Load bronze tables

# COMMAND ----------

# All reads are from Delta — no CSV parsing here.
student_vle        = spark.table("campus.bronze.student_vle")
student_info       = spark.table("campus.bronze.student_info")
student_assessment = spark.table("campus.bronze.student_assessment")
assessments        = spark.table("campus.bronze.assessments")
attendance_synth   = spark.table("campus.bronze.attendance_synth")

print("Bronze tables loaded.")
print(f"  student_vle        : {student_vle.count():>10,}")
print(f"  student_info       : {student_info.count():>10,}")
print(f"  student_assessment : {student_assessment.count():>10,}")
print(f"  assessments        : {assessments.count():>10,}")
print(f"  attendance_synth   : {attendance_synth.count():>10,}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 2 — Apply the six-week leakage cutoff to VLE data
# MAGIC
# MAGIC > **LEAKAGE CUTOFF — `date <= 42` applied exactly once, here.**
# MAGIC > This filter must never be re-derived, moved, or relaxed downstream.
# MAGIC > Training on full-term VLE behaviour leaks the outcome: the model
# MAGIC > scores beautifully on held-out data and warns nobody in time.
# MAGIC > — CLAUDE.md, Rule 1

# COMMAND ----------

# ── VLE: restrict to the six-week window ──────────────────────────────────
# `date` is a literal column name in studentVle — backtick it in SQL and
# in PySpark col() to avoid parser ambiguity.
#
# week = FLOOR(date / 7) + 1
#   date 0–6   → week 1
#   date 7–13  → week 2
#   date 14–20 → week 3
#   date 21–27 → week 4
#   date 28–34 → week 5
#   date 35–41 → week 6
#   date 42    → week 7 (day-42 rows fall off the 1–6 spine below)
#
# Pre-module activity (date < 0) produces week ≤ 0 and likewise falls off
# the spine. The filter ensures we never touch post-week-6 data.

vle_filtered = (
    student_vle
    .filter(F.col("`date`") <= 42)           # ← THE LEAKAGE CUTOFF — DO NOT MOVE
    .withColumn("week", (F.col("`date`") / 7).cast("int") + 1)
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 3 — VLE features per (student, module, presentation, week)

# COMMAND ----------

vle_features = (
    vle_filtered
    .groupBy("id_student", "code_module", "code_presentation", "week")
    .agg(
        F.sum("sum_click").cast("long").alias("clicks"),
        F.countDistinct("`date`").alias("active_days"),
        F.countDistinct("id_site").alias("distinct_resources"),
    )
)

print(f"VLE feature rows: {vle_features.count():,}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 4 — Assessment features per (student, module, presentation, week)
# MAGIC
# MAGIC `studentAssessment` has no module columns — only `id_assessment`.
# MAGIC Join to `assessments` on `id_assessment` first to obtain
# MAGIC `code_module`, `code_presentation`, and the assessment due date.
# MAGIC
# MAGIC `days_late_avg` = average of (date_submitted − assessment.`date`).
# MAGIC Positive → late. Negative → early. **NULL** → no submission in the window.
# MAGIC A missing submission and an on-time submission are not the same thing;
# MAGIC the column is left null rather than filled with 0.

# COMMAND ----------

# Join: studentAssessment → assessments (to get module and due date)
# `date` in assessments is the assessment due date; backtick in SQL / col().
assessment_joined = (
    student_assessment
    .join(
        assessments.select(
            "id_assessment",
            "code_module",
            "code_presentation",
            F.col("`date`").alias("due_date"),   # rename to avoid collision
        ),
        on="id_assessment",
        how="inner",
    )
    # Restrict to submissions within the six-week window.
    # (The VLE cutoff above is for VLE data; this filters submission dates.)
    .filter(F.col("date_submitted") <= 42)
    .withColumn("week", (F.col("date_submitted") / 7).cast("int") + 1)
    .withColumn(
        "days_late",
        (F.col("date_submitted").cast("double") - F.col("due_date").cast("double")),
    )
)

assessment_features = (
    assessment_joined
    .groupBy("id_student", "code_module", "code_presentation", "week")
    .agg(
        F.count("*").alias("submissions"),
        F.avg("score").alias("avg_score"),         # null when score is null
        F.avg("days_late").alias("days_late_avg"), # null preserved — not filled
    )
)

print(f"Assessment feature rows: {assessment_features.count():,}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 5 — Attendance features per (student, module, presentation, week)
# MAGIC
# MAGIC `attendance_synth` stores `session_date` as a day offset (same scale as
# MAGIC `date` in VLE).  Compute week the same way.  `sessions_held` is a
# MAGIC property of the *module-presentation*, not the individual student.

# COMMAND ----------

# sessions_held per (module, presentation, week) — same for every student
sessions_held_df = (
    attendance_synth
    .filter(F.col("session_date") <= 42)
    .withColumn("week", (F.col("session_date") / 7).cast("int") + 1)
    .groupBy("code_module", "code_presentation", "week")
    .agg(F.countDistinct("session_no").alias("sessions_held"))
)

# sessions_attended per (student, module, presentation, week)
sessions_attended_df = (
    attendance_synth
    .filter(F.col("session_date") <= 42)
    .withColumn("week", (F.col("session_date") / 7).cast("int") + 1)
    .groupBy("id_student", "code_module", "code_presentation", "week")
    .agg(F.sum("attended").cast("int").alias("sessions_attended"))
)

print(f"sessions_held rows   : {sessions_held_df.count():,}")
print(f"sessions_attended rows: {sessions_attended_df.count():,}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 6 — Derive is_at_risk from studentInfo
# MAGIC
# MAGIC Binary label: `Fail` or `Withdrawn` → 1, everything else → 0.
# MAGIC (Four-class prediction costs an hour we do not have — CLAUDE.md.)
# MAGIC
# MAGIC Demographics are read here solely to extract `final_result`.
# MAGIC They are **never** selected into the output.

# COMMAND ----------

# is_at_risk: one value per (student, module, presentation)
risk_labels = (
    student_info
    .select(
        "id_student",
        "code_module",
        "code_presentation",
        F.when(
            F.col("final_result").isin("Fail", "Withdrawn"), 1
        ).otherwise(0).alias("is_at_risk"),
    )
)

print(f"Risk label rows: {risk_labels.count():,}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 7 — Assemble student_week
# MAGIC
# MAGIC Grain: every enrolled student × every week 1–6.
# MAGIC Build the spine from `student_info` × `{1,2,3,4,5,6}` then left-join
# MAGIC all feature frames.  Students with no VLE activity in a week get clicks=0,
# MAGIC not a missing row.

# COMMAND ----------

# Week spine: 1–6
week_spine_df = spark.createDataFrame(
    [(w,) for w in range(1, 7)], ["week"]
)

# Enrollment spine: distinct (id_student, module, presentation)
enrollment_spine = (
    student_info
    .select("id_student", "code_module", "code_presentation")
    .distinct()
)

# Full spine: every student × every module-presentation × every week 1–6
full_spine = enrollment_spine.crossJoin(week_spine_df)

print(f"Full spine rows: {full_spine.count():,}  "
      f"(expect enrollments × 6 ≈ {enrollment_spine.count() * 6:,})")

# COMMAND ----------
# MAGIC %md
# MAGIC ### Join all features onto the spine

# COMMAND ----------

student_week = (
    full_spine

    # ── VLE features ─────────────────────────────────────────────────────
    .join(vle_features,
          on=["id_student", "code_module", "code_presentation", "week"],
          how="left")

    # ── Assessment features ───────────────────────────────────────────────
    .join(assessment_features,
          on=["id_student", "code_module", "code_presentation", "week"],
          how="left")

    # ── sessions_held (module-presentation level) ─────────────────────────
    .join(sessions_held_df,
          on=["code_module", "code_presentation", "week"],
          how="left")

    # ── sessions_attended (student level) ────────────────────────────────
    .join(sessions_attended_df,
          on=["id_student", "code_module", "code_presentation", "week"],
          how="left")

    # ── Risk label ────────────────────────────────────────────────────────
    .join(risk_labels,
          on=["id_student", "code_module", "code_presentation"],
          how="left")

    # ── Select exactly the silver contract columns ────────────────────────
    # id_student renamed to student_id HERE (CLAUDE.md §schema contract).
    # All six demographic columns are excluded by not being named below.
    .select(
        F.col("id_student").alias("student_id"),    # ← rename happens here
        F.col("code_module"),
        F.col("code_presentation"),
        F.col("week"),
        F.coalesce(F.col("clicks"),             F.lit(0)).cast("long").alias("clicks"),
        F.coalesce(F.col("active_days"),        F.lit(0)).cast("int").alias("active_days"),
        F.coalesce(F.col("distinct_resources"), F.lit(0)).cast("int").alias("distinct_resources"),
        F.coalesce(F.col("submissions"),        F.lit(0)).cast("int").alias("submissions"),
        F.col("avg_score"),       # null = no submission in window; do not fill
        F.col("days_late_avg"),   # null = no submission in window; do not fill
        F.coalesce(F.col("sessions_held"),      F.lit(0)).cast("int").alias("sessions_held"),
        F.coalesce(F.col("sessions_attended"),  F.lit(0)).cast("int").alias("sessions_attended"),
        F.coalesce(F.col("is_at_risk"),         F.lit(0)).cast("int").alias("is_at_risk"),
    )
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 8 — Write campus.silver.student_week

# COMMAND ----------

(
    student_week
    .write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.silver.student_week")
)

print("✓ Written campus.silver.student_week")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 9 — Add table and column comments
# MAGIC (Genie's answer quality is metadata quality — CLAUDE.md)

# COMMAND ----------

spark.sql("""
    COMMENT ON TABLE campus.silver.student_week IS
    'One row per (student_id, code_module, code_presentation, week) for weeks 1–6.
     Six-week leakage cutoff (date <= 42) applied exactly once in 03_silver.py.
     id_student renamed to student_id here; all six demographic columns dropped here.
     Behaviour-only features: clicks, active_days, distinct_resources, submissions,
     avg_score, days_late_avg, sessions_held, sessions_attended.
     Outcome label: is_at_risk (1 = Fail or Withdrawn, 0 = Pass or Distinction).'
""")

column_comments = {
    "student_id":          "Unique student identifier. Renamed from id_student (source column); rename happens in silver, not bronze.",
    "code_module":         "OULAD module code (e.g. AAA, BBB).",
    "code_presentation":   "Presentation identifier (e.g. 2013J).",
    "week":                "Week number within the six-week window (1–6). week = FLOOR(date/7)+1.",
    "clicks":              "Total VLE clicks by this student in this module-presentation-week. 0 if no activity.",
    "active_days":         "Distinct calendar days with any VLE activity in the week.",
    "distinct_resources":  "Distinct VLE resource sites (id_site) accessed in the week.",
    "submissions":         "Count of assessment submissions with date_submitted falling in this week. 0 if none.",
    "avg_score":           "Average score across submissions in this week. NULL if no submissions (not 0).",
    "days_late_avg":       "Average (date_submitted − assessment due date) in days. Positive=late, negative=early. NULL if no submissions.",
    "sessions_held":       "Number of scheduled attendance sessions in this module-presentation-week (from synthetic attendance table).",
    "sessions_attended":   "Number of sessions attended by this student in this week (from synthetic attendance table).",
    "is_at_risk":          "Binary outcome label: 1 if final_result is Fail or Withdrawn, 0 otherwise. Derived from studentInfo.final_result.",
}

for col_name, comment in column_comments.items():
    spark.sql(f"""
        ALTER TABLE campus.silver.student_week
        ALTER COLUMN {col_name}
        COMMENT '{comment}'
    """)

print("✓ Column comments set")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 10 — Validation: row count and class balance

# COMMAND ----------

total_rows = spark.table("campus.silver.student_week").count()
print(f"\ncampus.silver.student_week row count: {total_rows:,}")

class_balance = spark.sql("""
    SELECT
        is_at_risk,
        COUNT(*)                                            AS rows,
        ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS pct
    FROM campus.silver.student_week
    GROUP BY is_at_risk
    ORDER BY is_at_risk
""")

print("\nClass balance of is_at_risk:")
display(class_balance)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 11 — Demographic firewall assertion
# MAGIC
# MAGIC This cell **must fail loudly** if any demographic column has leaked into
# MAGIC silver.  Demographics die at the bronze → silver boundary.

# COMMAND ----------

DEMOGRAPHIC_COLS = {
    "gender",
    "region",
    "highest_education",
    "imd_band",
    "age_band",
    "disability",
}

silver_cols_lower = {
    c.name.lower()
    for c in spark.table("campus.silver.student_week").schema
}

leaked = DEMOGRAPHIC_COLS & silver_cols_lower

if leaked:
    raise AssertionError(
        f"\n\n"
        f"╔══════════════════════════════════════════════════════════════╗\n"
        f"║  DEMOGRAPHIC LEAK DETECTED — silver is contaminated!        ║\n"
        f"║                                                              ║\n"
        f"║  Column(s) found in campus.silver.student_week:             ║\n"
        f"║    {', '.join(sorted(leaked)):<56}║\n"
        f"║                                                              ║\n"
        f"║  Remove these columns from the SELECT in Step 7 and rerun.  ║\n"
        f"╚══════════════════════════════════════════════════════════════╝"
    )

print("✓ Demographic firewall check PASSED")
print(f"  Silver schema has {len(silver_cols_lower)} columns; "
      f"none of {sorted(DEMOGRAPHIC_COLS)} are present.")
print("\nFinal silver schema:")
spark.sql("DESCRIBE TABLE campus.silver.student_week").show(truncate=False)
