// Data layer.
//
// USE_LIVE_API=true talks to the FastAPI backend, which proxies Databricks with the
// signed-in user's own forwarded token so Unity Catalog row filters and column masks
// apply per-role. Set it to false to fall back to local demo data — kept deliberately
// as a judging-day parachute, because on Free Edition exceeding the fair-use quota
// kills compute for the rest of the day. Demo mode is always announced in the header.

export const USE_LIVE_API = true;
export const API_BASE = "/api";

// Band -> colour deliberately lives in CSS (data-band + --band-* tokens), not here, so
// light and dark themes can use different hues without a second source of truth in JS.

export const ROLE_LABEL = {
  advisor: "Advisor",
  student: "Student",
  dean: "Dean",
  admin: "Catalog admin",
};

async function withLatency(value, ms = 220) {
  await new Promise((r) => setTimeout(r, ms));
  return value;
}

async function getJSON(path) {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include" });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (_) { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return res.json();
}

// ---------- identity ----------

// role === null is a real answer, not a failure: it means the account has no row in
// campus.ops.role_map, so every gold query returns zero rows. The UI explains that
// rather than showing an error.
const MOCK_ME = {
  email: "demo.advisor@campus.edu",
  role: "advisor",
  advisor_id: "ADV001",
  student_id: null,
  department: "Computing and Information Technology",
};

export async function fetchMe() {
  if (!USE_LIVE_API) return withLatency(MOCK_ME);
  return getJSON("/me");
}

// ---------- cohort (advisor / admin) ----------

const MOCK_COHORT_RAW = [
  { id: "100893", name: "Bhavana Sharma", course: "AAA", riskBand: "high", riskScore: 0.6023, held: 6, att: 0, rem: 18, left: 0, band: "high",
    signals: ["consistent daily engagement in week 5", "engagement dropped from week 1 to week 6", "low average assessment score"] },
  { id: "74372", name: "Geeta Tiwari", course: "AAA", riskBand: "high", riskScore: 0.7148, held: 6, att: 1, rem: 18, left: 1, band: "high",
    signals: ["few active study days in week 6", "low average assessment score", "few active study days in week 5"] },
  { id: "30268", name: "Sowmya Nair", course: "AAA", riskBand: "high", riskScore: 0.7746, held: 6, att: 2, rem: 18, left: 2, band: "high",
    signals: ["few active study days in week 6", "low assessment submission count", "few active study days in week 5"] },
  { id: "65002", name: "Nisha Agarwal", course: "AAA", riskBand: "high", riskScore: 0.6645, held: 6, att: 3, rem: 18, left: 3, band: "medium",
    signals: ["few active study days in week 5", "few active study days in week 6"] },
  { id: "11391", name: "Sowmya Jain", course: "AAA", riskBand: "medium", riskScore: 0.4477, held: 6, att: 6, rem: 18, left: 6, band: "low",
    signals: ["sustained or improved engagement from week 1 to week 6", "few active study days in week 4"] },
];

function shapeCohort(rows) {
  return rows.map((s, i) => {
    const total = (s.held || 0) + (s.rem || 0);
    const thresholdPct = s.threshold_pct ?? 75;
    const needed = Math.ceil((thresholdPct / 100) * total);
    const pct = s.held ? (100 * s.att / s.held).toFixed(1) : "0.0";
    return {
      ...s,
      i,
      atRisk: s.atRisk ?? s.riskBand === "high",
      total,
      needed,
      pct,
      reachable: s.band !== "unavoidable",
      cost: s.cost || costFallback(s),
    };
  });
}

// Only used by demo mode — live rows carry gold's own cost_of_missing_next, which is
// authoritative and must never be recomputed client-side.
function costFallback(s) {
  const left = s.left ?? 0;
  if (left <= 0) return "Missing the next session makes the 75% attendance threshold mathematically unreachable — every remaining session must be attended.";
  const after = left - 1;
  return `Missing the next session reduces the buffer to ${after} ${after === 1 ? "session" : "sessions"}, keeping this student in the ${s.band} band.`;
}

export async function fetchCohort() {
  if (!USE_LIVE_API) return withLatency(shapeCohort(MOCK_COHORT_RAW));
  return shapeCohort(await getJSON("/gold/risk-signals"));
}

// ---------- student's own buffer ----------

const MOCK_SELF = {
  id: "28400", name: "Sneha Reddy", course: "AAA", band: "high", left: 1,
  held: 6, att: 1, rem: 18, threshold_pct: 75,
  cost: "Missing the next session reduces your buffer to 0 sessions, keeping you in the high band.",
};

function shapeSelf(s) {
  const total = (s.held || 0) + (s.rem || 0);
  const thresholdPct = s.threshold_pct ?? 75;
  const needed = Math.ceil((thresholdPct / 100) * total);
  const pct = s.held ? (100 * s.att / s.held).toFixed(1) : "0.0";
  return {
    ...s,
    total,
    needed,
    pct,
    thresholdPct,
    reachable: s.band !== "unavoidable",
  };
}

export async function fetchStudentSelf() {
  if (!USE_LIVE_API) return withLatency(shapeSelf(MOCK_SELF));
  return shapeSelf(await getJSON("/gold/attendance-buffers/me"));
}

// ---------- institution overview (dean / admin) ----------

const MOCK_OVERVIEW = {
  total: 30, atRisk: 5, atRiskPct: 16.666, modules: 1, departments: 1,
  riskBands: [{ band: "medium", n: 13 }, { band: "low", n: 12 }, { band: "high", n: 5 }],
  bufferBands: [{ band: "low", n: 14 }, { band: "high", n: 9 }, { band: "medium", n: 7 }],
  nextHeadcount: 238, nextSessionDate: 49,
  rows: [
    { id: "30268", name: "REDACTED", course: "AAA", riskBand: "high", riskScore: 0.7746 },
    { id: "74372", name: "REDACTED", course: "AAA", riskBand: "high", riskScore: 0.7148 },
    { id: "65002", name: "REDACTED", course: "AAA", riskBand: "high", riskScore: 0.6645 },
    { id: "28400", name: "REDACTED", course: "AAA", riskBand: "high", riskScore: 0.6465 },
    { id: "100893", name: "REDACTED", course: "AAA", riskBand: "high", riskScore: 0.6023 },
  ],
  namesRedacted: true,
};

export async function fetchOverview() {
  if (!USE_LIVE_API) return withLatency(MOCK_OVERVIEW);
  return getJSON("/gold/overview");
}

// ---------- Genie ----------

export async function askGenie(question) {
  if (!USE_LIVE_API) {
    return withLatency(
      `Demo mode — not connected to a Genie space, so "${question}" can't be answered live. ` +
      "With the backend wired up this returns a governed answer from the Genie Conversations API.",
      420
    );
  }
  const res = await fetch(`${API_BASE}/genie/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    credentials: "include",
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (_) { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return (await res.json()).answer;
}

// Suggested questions per role, phrased against the vocabulary the Genie space was
// actually curated for (sql/genie/instructions.md + example_questions.sql). "My
// students" needs no advisor filter — row-level security already restricts it.
export const SUGGESTIONS = {
  advisor: [
    "Which of my students are at risk this week?",
    "Who has the least attendance margin left?",
    "What does missing the next session cost my highest-risk student?",
    "Show the top contributing signals for my at-risk students",
  ],
  student: [
    "What does missing my next session cost me?",
    "How is my attendance tracking against the 75% threshold?",
    "How many sessions have I attended so far?",
  ],
  dean: [
    "How many students are at risk, by department?",
    "What is the at-risk share across the cohort?",
    "What is the expected headcount for the next session?",
  ],
  admin: [
    "How many students are at risk this week?",
    "What is the at-risk share across the cohort?",
    "Which students have the least attendance margin left?",
  ],
};

// ---------- static explainer content ----------

// The governance SQL below is quoted from sql/governance/01_functions.sql and
// 02_attach.sql. It must stay verbatim — an earlier version of this page displayed
// invented policy names (advisor_scope, dept_scope, student_self, current_user_advisor)
// that do not exist in the catalog, which is precisely the thing a judge would pull on.
export const GOVERNANCE = {
  lineage: [
    { layer: "BRONZE", what: "7 OULAD sources + generated attendance", note: "Raw Delta tables, one per source. Nothing cleaned, nothing joined." },
    { layer: "SILVER", what: "One row per student per week", note: "date <= 42 applied here, once. Nothing downstream can reintroduce late data." },
    { layer: "GOLD", what: "risk_signals · attendance_buffers · session_forecasts", note: "Buffers are arithmetic; the risk model is MLflow-registered." },
    { layer: "GENIE", what: "Natural-language agent over the three gold tables", note: "Unity Catalog enforces per-role visibility beneath it." },
  ],
  policies: [
    {
      name: "campus.ops.rf_risk(adv_id, stu_id)",
      kind: "ROW FILTER",
      on: "gold.risk_signals · gold.attendance_buffers",
      sql: `CREATE OR REPLACE FUNCTION campus.ops.rf_risk(adv_id STRING, stu_id STRING)
RETURNS BOOLEAN
RETURN
  EXISTS (SELECT 1 FROM campus.ops.role_map rm
          WHERE lower(rm.user_email) = lower(current_user())
            AND rm.role IN ('admin', 'dean'))
  OR EXISTS (SELECT 1 FROM campus.ops.role_map rm
             WHERE lower(rm.user_email) = lower(current_user())
               AND rm.role = 'advisor' AND rm.advisor_id = adv_id)
  OR EXISTS (SELECT 1 FROM campus.ops.role_map rm
             WHERE lower(rm.user_email) = lower(current_user())
               AND rm.role = 'student' AND rm.student_id = stu_id);`,
    },
    {
      name: "campus.ops.mask_name(name)",
      kind: "COLUMN MASK",
      on: "student_name on both per-student tables",
      sql: `CREATE OR REPLACE FUNCTION campus.ops.mask_name(name STRING)
RETURNS STRING
RETURN
  CASE
    WHEN EXISTS (SELECT 1 FROM campus.ops.role_map rm
                 WHERE lower(rm.user_email) = lower(current_user())
                   AND rm.role IN ('advisor', 'admin', 'student'))
    THEN name
    ELSE 'REDACTED'
  END;`,
    },
  ],
  effect: [
    { role: "admin", rows: "every row", names: "visible" },
    { role: "dean", rows: "every row", names: "'REDACTED'" },
    { role: "advisor", rows: "only their own advisor_id", names: "visible" },
    { role: "student", rows: "only their own student_id", names: "visible" },
    { role: "no role_map row", rows: "none", names: "'REDACTED'" },
  ],
  notBuilt: [
    { what: "AI-written-work detection", why: "Detectors are unreliable; false positives land on real students as cheating accusations." },
    { what: "Subject preference prediction", why: "No ground truth exists for whether a student enjoys a subject." },
    { what: "Per-student next-day absence", why: "Driven by factors absent from the data." },
  ],
};
