"""SQL against campus.gold.* / campus.ops.* and row-shaping into the JSON the frontend expects.

Every gold query below relies on Unity Catalog row filters/masks (sql/governance/*) to
restrict rows to the calling user — none of them add their own WHERE advisor_id=...
or WHERE department=... clause. That's the point of the governance layer: the same SQL
text returns different rows depending on who's asking, because execute_statement() is
called with that user's own forwarded token (see app.py).

What the policies actually do, from sql/governance/01_functions.sql:
  campus.ops.rf_risk(adv_id, stu_id)  row filter on risk_signals + attendance_buffers
    admin/dean -> all rows; advisor -> own advisor_id; student -> own student_id
  campus.ops.mask_name(name)          column mask on student_name
    advisor/admin/student -> real name; everyone else (i.e. dean) -> 'REDACTED'
Note dean is NOT department-scoped — it sees every row, and differs from admin only by
the name mask. campus.gold.session_forecasts is deliberately unprotected (aggregate).

"At risk" means risk_band = 'high' — rule 1 of sql/genie/instructions.md. Genie answers
with that definition, so the dashboard must use it too or the two contradict each other
on the same screen.

No colours are returned from here. Band -> colour is a presentation concern and lives in
the frontend, so the theme can change without touching SQL.
"""

from typing import Any

from databricks.sdk import WorkspaceClient

AT_RISK_BAND = "high"


def to_int(v: Any) -> int | None:
    return int(v) if v is not None else None


def to_float(v: Any) -> float | None:
    return float(v) if v is not None else None


def run_sql(client: WorkspaceClient, warehouse_id: str, statement: str) -> list[dict]:
    resp = client.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=statement,
        catalog="campus",
        wait_timeout="30s",
    )
    state = getattr(resp.status.state, "value", str(resp.status.state))
    if state != "SUCCEEDED":
        detail = getattr(resp.status, "error", None)
        raise RuntimeError(f"query did not succeed (state={state}): {detail}")
    columns = [c.name for c in resp.manifest.schema.columns]
    rows = resp.result.data_array or [] if resp.result else []
    return [dict(zip(columns, row)) for row in rows]


# ---------- who is asking: campus.ops.role_map ----------

# role_map is deliberately left unprotected (a filter function cannot read a table that
# itself has a filter), so any signed-in user can resolve their own row. lower() on both
# sides is not optional: per CLAUDE.md a case mismatch returns zero rows with no error,
# which would read as "this user has no role" rather than as a bug.
ME_SQL = """
SELECT current_user() AS email,
       (SELECT rm.role       FROM campus.ops.role_map rm WHERE lower(rm.user_email) = lower(current_user()) LIMIT 1) AS role,
       (SELECT rm.advisor_id FROM campus.ops.role_map rm WHERE lower(rm.user_email) = lower(current_user()) LIMIT 1) AS advisor_id,
       (SELECT rm.student_id FROM campus.ops.role_map rm WHERE lower(rm.user_email) = lower(current_user()) LIMIT 1) AS student_id,
       (SELECT rm.department FROM campus.ops.role_map rm WHERE lower(rm.user_email) = lower(current_user()) LIMIT 1) AS department
"""


def fetch_me(client: WorkspaceClient, warehouse_id: str) -> dict:
    rows = run_sql(client, warehouse_id, ME_SQL)
    if not rows:
        return {"email": None, "role": None, "advisor_id": None, "student_id": None, "department": None}
    row = rows[0]
    return {
        "email": row.get("email"),
        # role is None when the account has no role_map row — the frontend renders an
        # explainer for that, because it is exactly what UC enforcement looks like:
        # every gold query returns zero rows.
        "role": row.get("role"),
        "advisor_id": row.get("advisor_id"),
        "student_id": row.get("student_id"),
        "department": row.get("department"),
    }


# ---------- cohort: gold.risk_signals ⋈ gold.attendance_buffers ----------

COHORT_SQL = """
SELECT
  r.student_id, r.student_name, r.code_module, r.code_presentation,
  r.risk_band, r.risk_score,
  r.top_factor_1, r.top_factor_2, r.top_factor_3,
  b.sessions_held, b.sessions_attended, b.sessions_remaining,
  b.sessions_missable, b.buffer_band, b.cost_of_missing_next, b.threshold_pct
FROM campus.gold.risk_signals r
JOIN campus.gold.attendance_buffers b
  ON r.student_id = b.student_id AND r.code_module = b.code_module
ORDER BY CASE WHEN r.risk_band = 'high' THEN 0 ELSE 1 END,
         r.risk_score DESC,
         b.sessions_missable ASC
LIMIT 200
"""


def fetch_cohort(client: WorkspaceClient, warehouse_id: str) -> list[dict]:
    rows = run_sql(client, warehouse_id, COHORT_SQL)
    out = []
    for row in rows:
        signals = [row.get(f"top_factor_{i}") for i in (1, 2, 3)]
        out.append({
            "id": row["student_id"],
            "name": row["student_name"],
            "course": row["code_module"],
            "presentation": row.get("code_presentation"),
            "signals": [s for s in signals if s],
            "riskBand": row.get("risk_band"),
            "riskScore": to_float(row.get("risk_score")),
            "atRisk": row.get("risk_band") == AT_RISK_BAND,
            "band": row["buffer_band"],
            "left": to_int(row["sessions_missable"]),
            "cost": row["cost_of_missing_next"],
            "held": to_int(row["sessions_held"]),
            "att": to_int(row["sessions_attended"]),
            "rem": to_int(row["sessions_remaining"]),
            "threshold_pct": to_float(row.get("threshold_pct")),
        })
    return out


# ---------- student's own row: gold.attendance_buffers under rf_risk ----------

ATTENDANCE_SELF_SQL = """
SELECT student_id, student_name, code_module, sessions_held, sessions_attended,
       sessions_remaining, sessions_missable, buffer_band, cost_of_missing_next, threshold_pct
FROM campus.gold.attendance_buffers
LIMIT 1
"""
# LIMIT 1: the student view shows one course. A student enrolled in more than one module
# would only see the first row UC returns — add a code_module filter here if that changes.
# Deliberately does NOT touch gold.risk_signals: a student is never shown a risk flag
# (CLAUDE.md — flags go to an advisor for intervention, never to the student).


def fetch_attendance_self(client: WorkspaceClient, warehouse_id: str) -> dict | None:
    rows = run_sql(client, warehouse_id, ATTENDANCE_SELF_SQL)
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row["student_id"],
        "name": row["student_name"],
        "course": row["code_module"],
        "band": row["buffer_band"],
        "left": to_int(row["sessions_missable"]),
        "cost": row["cost_of_missing_next"],
        "held": to_int(row["sessions_held"]),
        "att": to_int(row["sessions_attended"]),
        "rem": to_int(row["sessions_remaining"]),
        "threshold_pct": to_float(row.get("threshold_pct")),
    }


# ---------- institution overview (dean / admin) ----------

OVERVIEW_TOTALS_SQL = f"""
SELECT COUNT(*) AS total,
       SUM(CASE WHEN risk_band = '{AT_RISK_BAND}' THEN 1 ELSE 0 END) AS at_risk,
       COUNT(DISTINCT code_module) AS modules,
       COUNT(DISTINCT department) AS departments
FROM campus.gold.risk_signals
"""

RISK_BANDS_SQL = """
SELECT risk_band AS band, COUNT(*) AS n
FROM campus.gold.risk_signals
GROUP BY risk_band
"""

BUFFER_BANDS_SQL = """
SELECT buffer_band AS band, COUNT(*) AS n
FROM campus.gold.attendance_buffers
GROUP BY buffer_band
"""

# session_date is a day-offset integer (this project's convention — see the `date <= 42`
# six-week-cutoff rule), not a calendar date, so there is no weekday to filter on. Take
# each module's earliest still-forecasted session and sum expected headcount across modules.
NEXT_HEADCOUNT_SQL = """
SELECT SUM(f.expected_headcount) AS headcount, MIN(f.session_date) AS session_date
FROM campus.gold.session_forecasts f
JOIN (
  SELECT code_module, MIN(session_date) AS next_date
  FROM campus.gold.session_forecasts
  GROUP BY code_module
) nxt ON f.code_module = nxt.code_module AND f.session_date = nxt.next_date
"""

# student_name is selected on purpose: for a dean the column mask rewrites it to the
# literal string 'REDACTED', so the UI can show the mask doing its job rather than just
# asserting that it is configured.
OVERVIEW_ROWS_SQL = """
SELECT student_id, student_name, code_module, risk_band, risk_score
FROM campus.gold.risk_signals
ORDER BY risk_score DESC
LIMIT 12
"""


BY_DEPARTMENT_SQL = f"""
SELECT department AS name,
       COUNT(*) AS total,
       SUM(CASE WHEN risk_band = '{AT_RISK_BAND}' THEN 1 ELSE 0 END) AS at_risk
FROM campus.gold.risk_signals
GROUP BY department
ORDER BY at_risk DESC
LIMIT 12
"""

BY_MODULE_SQL = f"""
SELECT code_module AS name,
       COUNT(*) AS total,
       SUM(CASE WHEN risk_band = '{AT_RISK_BAND}' THEN 1 ELSE 0 END) AS at_risk
FROM campus.gold.risk_signals
GROUP BY code_module
ORDER BY at_risk DESC
LIMIT 12
"""


def _breakdown(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        total = to_int(r["total"]) or 0
        at_risk = to_int(r["at_risk"]) or 0
        out.append({
            "name": r["name"],
            "total": total,
            "atRisk": at_risk,
            "pct": (100 * at_risk / total) if total else 0.0,
        })
    return out


def fetch_overview(client: WorkspaceClient, warehouse_id: str) -> dict:
    totals = run_sql(client, warehouse_id, OVERVIEW_TOTALS_SQL)
    risk_bands = run_sql(client, warehouse_id, RISK_BANDS_SQL)
    buffer_bands = run_sql(client, warehouse_id, BUFFER_BANDS_SQL)
    next_hc = run_sql(client, warehouse_id, NEXT_HEADCOUNT_SQL)
    rows = run_sql(client, warehouse_id, OVERVIEW_ROWS_SQL)
    by_department = _breakdown(run_sql(client, warehouse_id, BY_DEPARTMENT_SQL))
    by_module = _breakdown(run_sql(client, warehouse_id, BY_MODULE_SQL))

    total = (to_int(totals[0]["total"]) if totals else 0) or 0
    at_risk = (to_int(totals[0]["at_risk"]) if totals else 0) or 0
    modules = (to_int(totals[0]["modules"]) if totals else 0) or 0
    departments = (to_int(totals[0]["departments"]) if totals else 0) or 0

    headcount = None
    next_session_date = None
    if next_hc and next_hc[0].get("headcount") is not None:
        headcount = round(to_float(next_hc[0]["headcount"]))
        next_session_date = to_int(next_hc[0].get("session_date"))

    sample = [{
        "id": r["student_id"],
        "name": r.get("student_name"),
        "course": r["code_module"],
        "riskBand": r.get("risk_band"),
        "riskScore": to_float(r.get("risk_score")),
    } for r in rows]

    # Detect the mask from the data rather than from the caller's role: whatever the
    # policy actually did to student_name is what we report.
    names_redacted = any((s["name"] or "") == "REDACTED" for s in sample)

    return {
        "total": total,
        "atRisk": at_risk,
        "atRiskPct": (100 * at_risk / total) if total else 0.0,
        "modules": modules,
        "departments": departments,
        "riskBands": [{"band": b["band"], "n": to_int(b["n"])} for b in risk_bands],
        "bufferBands": [{"band": b["band"], "n": to_int(b["n"])} for b in buffer_bands],
        "byDepartment": by_department,
        "byModule": by_module,
        "nextHeadcount": headcount,
        "nextSessionDate": next_session_date,
        "rows": sample,
        "namesRedacted": names_redacted,
    }
