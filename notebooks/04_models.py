# Databricks notebook source
# MAGIC %md
# MAGIC # 04 — Models: Risk Classifier & Session Headcount Forecaster
# MAGIC
# MAGIC Two models, both logged to MLflow.
# MAGIC
# MAGIC | Model | Type | Output |
# MAGIC |---|---|---|
# MAGIC | A — Risk Classifier | LogisticRegression (sklearn) | Per-enrollment risk score + top 3 factors → `campus.silver.model_a_risk_scores` |
# MAGIC | B — Session Headcount Forecaster | Rolling mean + linear trend | Session forecasts → `campus.silver.model_b_session_forecasts` |
# MAGIC
# MAGIC `05_gold.py` reads both staging tables and writes the governed gold tables.
# MAGIC
# MAGIC **Per CLAUDE.md: LogisticRegression is the shipped model.  Do not swap it for
# MAGIC anything else without an explicit instruction.**
# MAGIC
# MAGIC **Owner:** Aditya  |  **Track A — Ask My Cohort (BMSCE Hackathon 2026)**

# COMMAND ----------

import re
import numpy as np
import pandas as pd
import mlflow
import mlflow.sklearn

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import roc_auc_score, precision_score, recall_score

from pyspark.sql import functions as F

RANDOM_SEED = 42
ROLLING_K   = 3   # rolling window for Model B

# MLflow experiment: per-user, in the workspace.
_current_user = spark.sql("SELECT current_user() AS u").collect()[0]["u"]
MLFLOW_EXPERIMENT = f"/Users/{_current_user}/ask-my-cohort"
mlflow.set_experiment(MLFLOW_EXPERIMENT)

print(f"MLflow experiment : {MLFLOW_EXPERIMENT}")
print(f"Random seed       : {RANDOM_SEED}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 1 — Load campus.silver.student_week

# COMMAND ----------

# The silver table is small enough to collect to the driver.
# Schema (verified from 03_silver.py output):
#   student_id, code_module, code_presentation, week,
#   clicks, active_days, distinct_resources, submissions,
#   avg_score, days_late_avg, sessions_held, sessions_attended, is_at_risk

sw = spark.table("campus.silver.student_week").toPandas()

print(f"Rows loaded      : {len(sw):,}")
print(f"Weeks present    : {sorted(sw['week'].unique())}")
print(f"Enrollments      : {sw[['student_id','code_module','code_presentation']].drop_duplicates().shape[0]:,}")
print(f"At-risk share    : {sw['is_at_risk'].max()}")  # confirm col present

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 2 — Feature Engineering
# MAGIC
# MAGIC Pivot `campus.silver.student_week` to **one row per enrollment**
# MAGIC (student_id + code_module + code_presentation).
# MAGIC
# MAGIC | Group | Features |
# MAGIC |---|---|
# MAGIC | Per-week VLE | `clicks_w1` … `clicks_w6`, `active_days_w1` … `active_days_w6` |
# MAGIC | Aggregate | `total_submissions`, `avg_score`, `days_late_avg`, `attendance_rate` |
# MAGIC | Trend | `click_slope` (OLS slope of clicks over weeks 1–6), `week6_below_week1` (binary) |
# MAGIC
# MAGIC `avg_score` and `days_late_avg` may be NaN (no submissions in the window).
# MAGIC The imputer in the training pipeline handles them; NaN is **not** filled with 0
# MAGIC here because a missing submission and a zero-score submission are different.

# COMMAND ----------

IDX = ["student_id", "code_module", "code_presentation"]

# ── Per-week clicks ────────────────────────────────────────────────────────
clicks_wide = (
    sw.pivot_table(
        index=IDX, columns="week", values="clicks",
        aggfunc="sum", fill_value=0,
    )
    .rename(columns=lambda w: f"clicks_w{int(w)}")
)

# ── Per-week active days ───────────────────────────────────────────────────
active_wide = (
    sw.pivot_table(
        index=IDX, columns="week", values="active_days",
        aggfunc="sum", fill_value=0,
    )
    .rename(columns=lambda w: f"active_days_w{int(w)}")
)

# ── Aggregate features ─────────────────────────────────────────────────────
agg = (
    sw.groupby(IDX)
    .agg(
        total_submissions=("submissions",       "sum"),
        avg_score        =("avg_score",         "mean"),   # NaN preserved
        days_late_avg    =("days_late_avg",      "mean"),   # NaN preserved
        sessions_held    =("sessions_held",      "sum"),
        sessions_attended=("sessions_attended",  "sum"),
        is_at_risk       =("is_at_risk",         "max"),    # 1 if any week says at-risk
    )
)
agg["attendance_rate"] = (
    agg["sessions_attended"] / agg["sessions_held"].clip(lower=1)
).round(4)

# ── Join into one feature frame ────────────────────────────────────────────
features = clicks_wide.join(active_wide).join(agg).reset_index()

# ── Click slope (vectorised OLS, no library) ────────────────────────────────
WEEKS = np.array([1.0, 2, 3, 4, 5, 6])
CLICK_COLS = [f"clicks_w{w}" for w in range(1, 7)]

C      = features[CLICK_COLS].values.astype(float)   # (n_enroll, 6)
w_sum  = WEEKS.sum()                                  # 21
w2_sum = (WEEKS ** 2).sum()                           # 91
n_w    = len(WEEKS)                                   # 6
denom  = n_w * w2_sum - w_sum ** 2                    # 105

features["click_slope"]       = (n_w * C.dot(WEEKS) - w_sum * C.sum(axis=1)) / denom
features["week6_below_week1"] = (features["clicks_w6"] < features["clicks_w1"]).astype(int)

print(f"Feature matrix : {features.shape[0]:,} enrollments × {features.shape[1]} columns")
print(f"NaN in avg_score     : {features['avg_score'].isna().sum():,}")
print(f"NaN in days_late_avg : {features['days_late_avg'].isna().sum():,}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 3 — Demographic Assertion (HARD GATE — runs at 13:00 on build day)
# MAGIC
# MAGIC **This cell must fail loudly if any feature name contains a demographic keyword.**
# MAGIC If it fails, stop everything and fix the feature engineering.

# COMMAND ----------

# Features used for training — exclude the target and components of attendance_rate.
EXCLUDE      = {"is_at_risk", "sessions_held", "sessions_attended"}
FEATURE_COLS = [c for c in features.columns if c not in EXCLUDE and c not in IDX]

# ── HARD GATE ──────────────────────────────────────────────────────────────
BANNED_TERMS = {"gender", "region", "age", "disability", "imd", "education"}
flagged = [f for f in FEATURE_COLS if any(b in f.lower() for b in BANNED_TERMS)]

if flagged:
    raise AssertionError(
        f"\n\n"
        f"╔═══════════════════════════════════════════════════════════════════╗\n"
        f"║  DEMOGRAPHIC FEATURES DETECTED — HARD GATE FAILED               ║\n"
        f"║                                                                   ║\n"
        f"║  The following features contain banned demographic terms:        ║\n"
        f"║    {', '.join(flagged):<63}║\n"
        f"║                                                                   ║\n"
        f"║  Remove them from feature engineering before proceeding.         ║\n"
        f"║  No model may be trained until this cell passes.                 ║\n"
        f"╚═══════════════════════════════════════════════════════════════════╝"
    )

print("✓ Demographic gate PASSED — no demographic terms in any feature name.")
print(f"\n{len(FEATURE_COLS)} features approved for training:")
for f in FEATURE_COLS:
    print(f"  {f}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Model A — Risk Classifier (LogisticRegression)
# MAGIC
# MAGIC Per CLAUDE.md, **this is the model we ship**. Do not replace it with
# MAGIC GBT or any other model without an explicit team decision recorded in the doc.
# MAGIC
# MAGIC Pipeline: median imputation → StandardScaler → LogisticRegression(balanced).
# MAGIC Metrics are from 5-fold stratified cross-validation (not in-sample).

# COMMAND ----------

X = features[FEATURE_COLS].copy()
y = features["is_at_risk"].values

print(f"Training set: {len(y):,} enrollments, "
      f"{y.mean():.1%} at-risk, {(1-y.mean()):.1%} not at-risk")

pipe_A = Pipeline([
    ("imputer", SimpleImputer(strategy="median")),
    ("scaler",  StandardScaler()),
    ("clf",     LogisticRegression(
                    class_weight="balanced",
                    max_iter=1000,
                    random_state=RANDOM_SEED,
                )),
])

cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_SEED)

print("Running 5-fold cross-validation …")
y_proba_cv = cross_val_predict(pipe_A, X, y, cv=cv, method="predict_proba")[:, 1]
y_pred_cv  = (y_proba_cv >= 0.5).astype(int)

auc  = roc_auc_score(y, y_proba_cv)
prec = precision_score(y, y_pred_cv, zero_division=0)
rec  = recall_score(y, y_pred_cv, zero_division=0)

print(f"\n  ROC-AUC   : {auc:.4f}")
print(f"  Precision : {prec:.4f}")
print(f"  Recall    : {rec:.4f}")

# Fit on full dataset — needed for coefficients and scoring all enrollments.
pipe_A.fit(X, y)
print("\nPipeline fitted on full dataset.")

# COMMAND ----------
# MAGIC %md
# MAGIC ### Log Model A to MLflow

# COMMAND ----------

with mlflow.start_run(run_name="risk_classifier_logistic_regression") as run_A:

    mlflow.set_tag("model_role",    "production_risk_classifier")
    mlflow.set_tag("per_claude_md", "shipped_model_do_not_replace")

    mlflow.log_params({
        "model_type":    "LogisticRegression",
        "class_weight":  "balanced",
        "max_iter":      1000,
        "imputation":    "median",
        "cv_folds":      5,
        "n_features":    len(FEATURE_COLS),
        "n_enrollments": int(len(y)),
        "pct_at_risk":   round(float(y.mean()), 4),
    })
    mlflow.log_metrics({
        "cv_roc_auc":   round(auc,  4),
        "cv_precision": round(prec, 4),
        "cv_recall":    round(rec,  4),
    })

    # Feature list as a plain-text artifact
    feat_path = "/tmp/feature_list.txt"
    with open(feat_path, "w") as fh:
        fh.write("\n".join(FEATURE_COLS))
    mlflow.log_artifact(feat_path, artifact_path="features")

    # Persist the full fitted pipeline
    mlflow.sklearn.log_model(pipe_A, artifact_path="risk_classifier")

    run_id_A = run_A.info.run_id

print(f"✓ Model A logged.  Run ID: {run_id_A}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 4 — Per-Student Top 3 Contributing Factors
# MAGIC
# MAGIC Contribution of feature *i* for student *j*:
# MAGIC ```
# MAGIC   contribution[j, i] = coefficient[i] × standardised_value[j, i]
# MAGIC ```
# MAGIC Positive → pushes toward at-risk.  Negative → protective.
# MAGIC Top 3 by absolute value are stored as plain-English strings that
# MAGIC Genie can read directly to a faculty advisor.

# COMMAND ----------

# Extract imputed + scaled matrix using the already-fitted pipeline steps.
X_imputed = pipe_A.named_steps["imputer"].transform(X)
X_scaled  = pipe_A.named_steps["scaler"].transform(X_imputed)
coefs     = pipe_A.named_steps["clf"].coef_[0]       # shape (n_features,)
contribs  = X_scaled * coefs                          # shape (n_enroll, n_features)
risk_scores_arr = pipe_A.predict_proba(X)[:, 1]      # P(at_risk=1)

# ── Feature → plain-English label ─────────────────────────────────────────
def _factor_label(feat: str, contrib_val: float) -> str:
    """
    Return a plain-English label for this feature's contribution.
    Positive contrib → factor is increasing risk (use the 'risk' phrase).
    Negative contrib → factor is protective (use the 'positive' phrase).
    """
    risk_positive = contrib_val > 0

    # Per-week clicks
    m = re.match(r"clicks_w(\d+)$", feat)
    if m:
        w = m.group(1)
        return (f"low VLE engagement in week {w}"
                if risk_positive else
                f"strong VLE engagement in week {w}")

    # Per-week active days
    m = re.match(r"active_days_w(\d+)$", feat)
    if m:
        w = m.group(1)
        return (f"few active study days in week {w}"
                if risk_positive else
                f"consistent daily engagement in week {w}")

    _labels = {
        "total_submissions": (
            "low assessment submission count",
            "good assessment submission rate",
        ),
        "avg_score": (
            "low average assessment score",
            "strong average assessment score",
        ),
        "days_late_avg": (
            "pattern of late assessment submissions",
            "timely assessment submission pattern",
        ),
        "attendance_rate": (
            "low session attendance",
            "strong session attendance",
        ),
        "click_slope": (
            "declining VLE engagement trend across weeks 1–6",
            "growing VLE engagement trend across weeks 1–6",
        ),
        "week6_below_week1": (
            "engagement dropped from week 1 to week 6",
            "sustained or improved engagement from week 1 to week 6",
        ),
    }
    if feat in _labels:
        return _labels[feat][0 if risk_positive else 1]
    return feat   # fallback: raw feature name

# ── Compute top 3 per enrollment ──────────────────────────────────────────
top_factor_rows = []
for i in range(len(features)):
    row_c   = contribs[i]
    top_idx = np.argsort(np.abs(row_c))[::-1][:3]
    factors = [_factor_label(FEATURE_COLS[j], row_c[j]) for j in top_idx]

    top_factor_rows.append({
        "student_id":    features.iloc[i]["student_id"],
        "code_module":   features.iloc[i]["code_module"],
        "code_presentation": features.iloc[i]["code_presentation"],
        "risk_score":    round(float(risk_scores_arr[i]), 4),
        "top_factor_1":  factors[0],
        "top_factor_2":  factors[1],
        "top_factor_3":  factors[2],
    })

risk_scores_pd = pd.DataFrame(top_factor_rows)

print(f"Scored {len(risk_scores_pd):,} enrollments.")
print("\nSample (highest-risk 5 enrollments):")
display(
    risk_scores_pd.nlargest(5, "risk_score")[
        ["student_id", "code_module", "risk_score",
         "top_factor_1", "top_factor_2", "top_factor_3"]
    ]
)

# COMMAND ----------
# MAGIC %md
# MAGIC ### Save Model A scores → campus.silver.model_a_risk_scores
# MAGIC
# MAGIC `05_gold.py` will join this with `ops.role_map` to add `student_name`,
# MAGIC `advisor_id`, `department`, compute `risk_band`, and write `gold.risk_signals`.

# COMMAND ----------

(
    spark.createDataFrame(risk_scores_pd)
    .write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.silver.model_a_risk_scores")
)
print("✓ Saved campus.silver.model_a_risk_scores")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Model B — Session Headcount Forecaster
# MAGIC
# MAGIC **Aggregate model — not per-student.**
# MAGIC For each `(code_module, code_presentation)`:
# MAGIC 1. Compute actual headcount per session from `attendance_synth`.
# MAGIC 2. Fit a linear trend (OLS, no library) over the observed sessions.
# MAGIC 3. Predict upcoming sessions using **rolling mean of last k={ROLLING_K} sessions
# MAGIC    plus a trend correction** of `slope × steps_ahead`.
# MAGIC 4. `lower_bound` / `upper_bound` from `predicted ± 1.96 × residual_std`.
# MAGIC
# MAGIC "Observed" sessions = sessions 1–6 (session_date ≤ 42, matching the six-week
# MAGIC window).  Sessions 7–24 are the upcoming sessions to forecast.

# COMMAND ----------

attendance = spark.table("campus.bronze.attendance_synth").toPandas()

# Actual headcount per session
headcount = (
    attendance
    .groupby(["code_module", "code_presentation", "session_no", "session_date"])
    .agg(actual_headcount=("attended", "sum"))
    .reset_index()
    .sort_values(["code_module", "code_presentation", "session_no"])
    .reset_index(drop=True)
)

print(f"attendance_synth rows : {len(attendance):,}")
print(f"headcount rows (all sessions) : {len(headcount):,}")
print(f"Distinct module-presentations : {headcount[['code_module','code_presentation']].drop_duplicates().shape[0]}")

# COMMAND ----------
# MAGIC %md
# MAGIC ### Fit rolling mean + trend, generate forecasts

# COMMAND ----------

forecast_rows = []
in_sample_errors = []   # for MAE metric

for (mod, pres), grp in headcount.groupby(["code_module", "code_presentation"]):
    grp = grp.sort_values("session_no").reset_index(drop=True)

    sessions = grp["session_no"].values.astype(float)
    counts   = grp["actual_headcount"].values.astype(float)
    n        = len(sessions)

    # ── Linear trend (OLS) over ALL sessions ─────────────────────────────
    # slope, intercept from np.polyfit — stdlib numpy, not a TS library.
    slope, intercept = np.polyfit(sessions, counts, 1)
    trend_pred  = intercept + slope * sessions
    residuals   = counts - trend_pred
    resid_std   = float(np.std(residuals, ddof=1)) if n > 1 else 1.0

    # ── Observed vs upcoming split ────────────────────────────────────────
    observed = grp[grp["session_no"] <= 6].reset_index(drop=True)
    upcoming = grp[grp["session_no"] > 6].reset_index(drop=True)

    if len(observed) == 0:
        continue

    last_known = int(observed["session_no"].max())

    # ── In-sample rolling-window MAE (for MLflow metric) ─────────────────
    for idx in range(ROLLING_K, len(observed)):
        window_mean = observed["actual_headcount"].iloc[idx - ROLLING_K : idx].mean()
        steps = observed["session_no"].iloc[idx] - observed["session_no"].iloc[idx - 1]
        pred_i = window_mean + slope * steps
        in_sample_errors.append(abs(pred_i - observed["actual_headcount"].iloc[idx]))

    # ── Forecast upcoming sessions ────────────────────────────────────────
    last_k_mean = observed["actual_headcount"].tail(ROLLING_K).mean()

    for _, row in upcoming.iterrows():
        steps_ahead = int(row["session_no"]) - last_known
        predicted   = max(0.0, last_k_mean + slope * steps_ahead)
        lower       = max(0.0, predicted - 1.96 * resid_std)
        upper       = predicted + 1.96 * resid_std

        forecast_rows.append({
            "code_module":        mod,
            "code_presentation":  pres,
            "session_date":       int(row["session_date"]),
            "expected_headcount": round(predicted, 1),
            "lower_bound":        round(lower, 1),
            "upper_bound":        round(upper, 1),
            "model_version":      "rolling_mean_trend_v1",
        })

forecast_pd  = pd.DataFrame(forecast_rows)
overall_mae  = float(np.mean(in_sample_errors)) if in_sample_errors else float("nan")

print(f"Forecast rows generated  : {len(forecast_pd):,}")
print(f"In-sample MAE (sessions) : {overall_mae:.2f} attendees")
print("\nSample forecasts:")
display(forecast_pd.head(10))

# COMMAND ----------
# MAGIC %md
# MAGIC ### Log Model B to MLflow

# COMMAND ----------

with mlflow.start_run(run_name="session_headcount_forecaster") as run_B:

    mlflow.set_tag("model_role", "session_headcount_forecaster")

    mlflow.log_params({
        "model_type":          "RollingMeanPlusLinearTrend",
        "rolling_window_k":    ROLLING_K,
        "confidence_level":    0.95,
        "bounds_method":       "1.96_sigma",
        "observed_sessions":   "1-6 (session_date <= 42)",
        "forecast_sessions":   "7-24",
    })
    mlflow.log_metrics({
        "in_sample_mae":  round(overall_mae, 4),
        "n_forecasts":    len(forecast_pd),
    })

    fc_path = "/tmp/session_forecasts.csv"
    forecast_pd.to_csv(fc_path, index=False)
    mlflow.log_artifact(fc_path, artifact_path="forecasts")

    run_id_B = run_B.info.run_id

print(f"✓ Model B logged.  Run ID: {run_id_B}")

# COMMAND ----------
# MAGIC %md
# MAGIC ### Save Model B forecasts → campus.silver.model_b_session_forecasts
# MAGIC
# MAGIC `05_gold.py` reads this and writes `gold.session_forecasts` with
# MAGIC `COMMENT ON TABLE` / `COMMENT ON COLUMN` metadata for Genie.

# COMMAND ----------

(
    spark.createDataFrame(forecast_pd)
    .write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("campus.silver.model_b_session_forecasts")
)
print("✓ Saved campus.silver.model_b_session_forecasts")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

summary = {
    "Model A — Risk Classifier": {
        "run_id":       run_id_A,
        "cv_roc_auc":   round(auc,  4),
        "cv_precision": round(prec, 4),
        "cv_recall":    round(rec,  4),
        "n_enrollments": len(y),
        "n_features":    len(FEATURE_COLS),
        "output_table":  "campus.silver.model_a_risk_scores",
    },
    "Model B — Session Forecaster": {
        "run_id":           run_id_B,
        "in_sample_mae":    round(overall_mae, 2),
        "rolling_window_k": ROLLING_K,
        "n_forecast_rows":  len(forecast_pd),
        "output_table":     "campus.silver.model_b_session_forecasts",
    },
}

for model, info in summary.items():
    print(f"\n{model}")
    print("-" * 50)
    for k, v in info.items():
        print(f"  {k:<22}: {v}")

print(
    "\n\nNext step → run 05_gold.py to:\n"
    "  1. Join model_a_risk_scores with ops.role_map → gold.risk_signals\n"
    "  2. Promote model_b_session_forecasts         → gold.session_forecasts\n"
    "  3. Compute attendance_buffers                → gold.attendance_buffers\n"
    "  4. Apply COMMENT ON TABLE / COLUMN everywhere (Genie reads metadata)."
)
