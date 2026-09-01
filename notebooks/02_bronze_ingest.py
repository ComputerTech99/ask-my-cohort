# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Bronze Ingest
# MAGIC
# MAGIC Reads the seven raw OULAD CSVs from the Unity Catalog volume and writes
# MAGIC one Delta table per file into **`campus.bronze`**.
# MAGIC
# MAGIC **Bronze is raw by definition.**  No cleaning, no filtering, no renaming.
# MAGIC Column names match the source files exactly — including `id_student` and
# MAGIC all six demographic columns in `student_info`.  Demographics are dropped
# MAGIC at the silver boundary, not here.
# MAGIC
# MAGIC `attendance_synth` is already in bronze (written by `01_generate_attendance.py`).
# MAGIC This notebook handles the seven OULAD source files only.
# MAGIC
# MAGIC **Owner:** Aditya  |  **Track A — Ask My Cohort (BMSCE Hackathon 2026)**

# COMMAND ----------

# ── Constants ──────────────────────────────────────────────────────────────
# Source volume path.  All seven CSVs live here.
RAW_PATH = "/Volumes/campus/bronze/raw/"

# Common read options: the source CSVs are quote-wrapped (verified from
# data/headers/*.csv samples).  inferSchema avoids a separate schema file.
READ_OPTS = {
    "header":      "true",
    "inferSchema": "true",
    "quote":       '"',
}

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1 — assessments.csv
# MAGIC Columns (verified from data/headers/assessments_head.csv):
# MAGIC `code_module`, `code_presentation`, `id_assessment`, `assessment_type`,
# MAGIC `date`, `weight`

# COMMAND ----------

assessments = (
    spark.read
    .options(**READ_OPTS)
    .csv(RAW_PATH + "assessments.csv")
)

(
    assessments.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.bronze.assessments")
)

print(f"campus.bronze.assessments  : {assessments.count():>10,} rows")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2 — courses.csv
# MAGIC Columns (verified from data/headers/courses_head.csv):
# MAGIC `code_module`, `code_presentation`, `module_presentation_length`

# COMMAND ----------

courses = (
    spark.read
    .options(**READ_OPTS)
    .csv(RAW_PATH + "courses.csv")
)

(
    courses.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.bronze.courses")
)

print(f"campus.bronze.courses      : {courses.count():>10,} rows")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3 — studentAssessment.csv
# MAGIC Columns (verified from data/headers/studentAssessment_head.csv):
# MAGIC `id_assessment`, `id_student`, `date_submitted`, `is_banked`, `score`
# MAGIC
# MAGIC **Note:** this file has NO `code_module` or `code_presentation`.
# MAGIC Reaching a module from a submission requires joining through
# MAGIC `assessments` on `id_assessment` first.

# COMMAND ----------

student_assessment = (
    spark.read
    .options(**READ_OPTS)
    .csv(RAW_PATH + "studentAssessment.csv")
)

(
    student_assessment.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.bronze.student_assessment")
)

print(f"campus.bronze.student_assessment : {student_assessment.count():>10,} rows")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4 — studentInfo.csv
# MAGIC Columns (verified from data/headers/studentInfo_head.csv):
# MAGIC `code_module`, `code_presentation`, `id_student`, `gender`, `region`,
# MAGIC `highest_education`, `imd_band`, `age_band`, `num_of_prev_attempts`,
# MAGIC `studied_credits`, `disability`, `final_result`
# MAGIC
# MAGIC Demographics load here because **bronze is raw**.  They are dropped at
# MAGIC the silver boundary in `03_silver.py`.

# COMMAND ----------

student_info = (
    spark.read
    .options(**READ_OPTS)
    .csv(RAW_PATH + "studentInfo.csv")
)

(
    student_info.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.bronze.student_info")
)

print(f"campus.bronze.student_info : {student_info.count():>10,} rows")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5 — studentRegistration.csv
# MAGIC Columns (verified from data/headers/studentRegistration_head.csv):
# MAGIC `code_module`, `code_presentation`, `id_student`,
# MAGIC `date_registration`, `date_unregistration`
# MAGIC
# MAGIC `date_unregistration` is null for students who never withdrew.
# MAGIC That null is information — do not fill it.

# COMMAND ----------

student_registration = (
    spark.read
    .options(**READ_OPTS)
    .csv(RAW_PATH + "studentRegistration.csv")
)

(
    student_registration.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.bronze.student_registration")
)

print(f"campus.bronze.student_registration : {student_registration.count():>10,} rows")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 6 — studentVle.csv.gz  (largest file, ~10 M rows)
# MAGIC Columns (verified from data/headers/studentVle_head.csv):
# MAGIC `code_module`, `code_presentation`, `id_student`, `id_site`,
# MAGIC `date`, `sum_click`
# MAGIC
# MAGIC The glob `studentVle.csv*` matches the gzipped file, an uncompressed
# MAGIC copy, and any split parts — whichever is present in the volume — without
# MAGIC a code change.  Spark decompresses `.gz` natively.

# COMMAND ----------

student_vle = (
    spark.read
    .options(**READ_OPTS)
    .csv(RAW_PATH + "studentVle.csv*")   # glob: .csv, .csv.gz, .csv.part-*.gz etc.
)

(
    student_vle.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.bronze.student_vle")
)

print(f"campus.bronze.student_vle  : {student_vle.count():>10,} rows  (expected ~10 M)")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 7 — vle.csv
# MAGIC Columns (verified from data/headers/vle_head.csv):
# MAGIC `id_site`, `code_module`, `code_presentation`, `activity_type`,
# MAGIC `week_from`, `week_to`

# COMMAND ----------

vle = (
    spark.read
    .options(**READ_OPTS)
    .csv(RAW_PATH + "vle.csv")
)

(
    vle.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.bronze.vle")
)

print(f"campus.bronze.vle          : {vle.count():>10,} rows")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Row-count summary — all eight bronze tables

# COMMAND ----------

bronze_tables = [
    "assessments",
    "courses",
    "student_assessment",
    "student_info",
    "student_registration",
    "student_vle",
    "vle",
    "attendance_synth",   # written by 01_generate_attendance.py
]

print("\nBronze table row counts")
print("-" * 46)
for tbl in bronze_tables:
    n = spark.table(f"campus.bronze.{tbl}").count()
    print(f"  campus.bronze.{tbl:<26}  {n:>10,}")

print("-" * 46)
print("All eight bronze tables confirmed.\n")
print("Note: student_vle is expected to be the largest (~10 M rows).")
print("      student_info is expected ~32 K rows (one per enrollment).")
