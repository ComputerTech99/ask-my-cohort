# Databricks notebook source
# MAGIC %md
# MAGIC # 00 — Catalog & Schema Setup
# MAGIC
# MAGIC Creates the `campus` Unity Catalog catalog and the four schemas
# MAGIC (`bronze`, `silver`, `gold`, `ops`) if they do not already exist.
# MAGIC Run once at the start of build day from a **serverless warehouse** or
# MAGIC any attached cluster with Unity Catalog enabled.
# MAGIC
# MAGIC **Owner:** Aditya  |  **Track A — Ask My Cohort (BMSCE Hackathon 2026)**

# COMMAND ----------

# Print the identity that will own the objects — useful for debugging
# permission errors later in the day.
display(spark.sql("SELECT current_user() AS running_as"))

# COMMAND ----------

# ── Catalog ────────────────────────────────────────────────────────────────
spark.sql("""
    CREATE CATALOG IF NOT EXISTS campus
    COMMENT 'Ask-My-Cohort: governed campus data for the BMSCE Databricks Hackathon 2026'
""")

print("✓ catalog `campus` ready")

# COMMAND ----------

# ── Schemas ────────────────────────────────────────────────────────────────
# bronze  — raw ingest, source column names kept as-is, demographics included
# silver  — filtered (date <= 42), id_student renamed to student_id,
#            demographics dropped
# gold    — model outputs consumed by Genie; row filters and column masks
#            attached by Ojash in sql/governance/
# ops     — role_map for current_user()-based access control (no filter on
#            this table — filter functions cannot read a filtered table)

for schema, comment in [
    ("bronze",
     "Raw OULAD ingest plus synthetic attendance. "
     "Source column names unchanged. Demographics present."),
    ("silver",
     "Six-week feature window (date <= 42 applied here). "
     "id_student renamed to student_id. Demographics dropped."),
    ("gold",
     "Risk signals, session forecasts and attendance buffers consumed by "
     "Genie. Row filters and column masks applied by governance DDL."),
    ("ops",
     "Operational tables: role_map for current_user() access control. "
     "Never attach row filters to this schema — filter functions cannot "
     "query a table that itself has a filter."),
]:
    spark.sql(f"""
        CREATE SCHEMA IF NOT EXISTS campus.{schema}
        COMMENT '{comment}'
    """)
    print(f"✓ schema `campus.{schema}` ready")

# COMMAND ----------

# ── Confirm: list all schemas in the catalog ───────────────────────────────
print("\nSchemas in catalog `campus`:")
display(spark.sql("SHOW SCHEMAS IN campus"))
