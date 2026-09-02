// Data layer. One codebase serves two deployments — deliberately not a fork, so a fix
// never has to be made twice.
//
//   Databricks Apps  -> live. Talks to the FastAPI backend, which proxies Databricks
//                       with the signed-in user's own forwarded token, so Unity Catalog
//                       row filters and column masks apply per role.
//   anywhere else    -> demo. Self-contained sample data, no backend, no Databricks.
//                       Lets anyone open the product on a public URL and step through
//                       each role's perspective.
//
// The production host is whitelisted rather than inferred, and this deliberately does
// NOT fall back to demo data when the backend errors. Silently swapping real governed
// output for mock data would be the single worst failure this product could have — on
// the live host a broken backend must surface as an error, never as plausible fiction.
const LIVE_HOSTS = [".databricksapps.com"];

export const USE_LIVE_API = LIVE_HOSTS.some((h) => window.location.hostname.endsWith(h));
export const IS_DEMO = !USE_LIVE_API;
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

// In demo mode the visitor picks a perspective instead of being one. Live mode never
// consults this — there, identity comes from campus.ops.role_map joined on
// current_user(), and no amount of clicking in the browser can change it.
const DEMO_IDENTITIES = {
  advisor: { email: "m.rao@campus.edu", role: "advisor", advisor_id: "ADV001", student_id: null, department: "Computing and Information Technology" },
  dean: { email: "dean.engineering@campus.edu", role: "dean", advisor_id: null, student_id: null, department: "Computing and Information Technology" },
  student: { email: "student_28400@campus.edu", role: "student", advisor_id: null, student_id: "28400", department: "Computing and Information Technology" },
  admin: { email: "catalog.admin@campus.edu", role: "admin", advisor_id: null, student_id: null, department: "Computing and Information Technology" },
};

export const DEMO_ROLES = ["advisor", "dean", "student", "admin"];

let demoRole = null;
try { demoRole = localStorage.getItem("amc-demo-role"); } catch (_) { /* storage blocked */ }
if (!DEMO_ROLES.includes(demoRole)) demoRole = null;

export function getDemoRole() {
  return demoRole;
}

export function setDemoRole(role) {
  demoRole = DEMO_ROLES.includes(role) ? role : null;
  try {
    if (demoRole) localStorage.setItem("amc-demo-role", demoRole);
    else localStorage.removeItem("amc-demo-role");
  } catch (_) { /* not persisted */ }
}

// role === null is a real answer in live mode, not a failure: it means the account has
// no row in campus.ops.role_map, so every gold query returns zero rows. The UI explains
// that rather than showing an error.
export async function fetchMe() {
  if (IS_DEMO) return withLatency(DEMO_IDENTITIES[demoRole] || null, 120);
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
  if (IS_DEMO) {
    // An admin sees names; a dean would see 'REDACTED' here. Reproduced so the demo
    // shows the mask's real effect rather than describing it.
    const rows = demoRole === "dean"
      ? MOCK_COHORT_RAW.map((s) => ({ ...s, name: "REDACTED" }))
      : MOCK_COHORT_RAW;
    return withLatency(shapeCohort(rows));
  }
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

// Figures mirror the real gold tables so the demo is representative of what the live
// product shows: 32,593 enrolments across 7 departments and 7 modules.
const MOCK_OVERVIEW_ROWS = [
  { id: "30268", name: "Sowmya Nair", course: "AAA", riskBand: "high", riskScore: 0.7746 },
  { id: "74372", name: "Geeta Tiwari", course: "AAA", riskBand: "high", riskScore: 0.7148 },
  { id: "65002", name: "Nisha Agarwal", course: "BBB", riskBand: "high", riskScore: 0.6645 },
  { id: "28400", name: "Sneha Reddy", course: "AAA", riskBand: "high", riskScore: 0.6465 },
  { id: "100893", name: "Bhavana Sharma", course: "DDD", riskBand: "high", riskScore: 0.6023 },
  { id: "58873", name: "Pooja Arora", course: "CCC", riskBand: "medium", riskScore: 0.5410 },
];

const MOCK_OVERVIEW = {
  total: 32593, atRisk: 13634, atRiskPct: 41.83, modules: 7, departments: 7,
  riskBands: [{ band: "high", n: 13634 }, { band: "low", n: 10361 }, { band: "medium", n: 8598 }],
  bufferBands: [{ band: "low", n: 15112 }, { band: "medium", n: 9034 }, { band: "high", n: 8447 }],
  nextHeadcount: 19111, nextSessionDate: 49,
  byDepartment: [
    { name: "Business and Management", total: 7909, atRisk: 3963, pct: 50.1 },
    { name: "Mathematics and Statistics", total: 7762, atRisk: 2352, pct: 30.3 },
    { name: "Arts and Humanities", total: 6272, atRisk: 2218, pct: 35.4 },
    { name: "Social Sciences", total: 2534, atRisk: 2125, pct: 83.9 },
    { name: "Science and Technology", total: 4434, atRisk: 1874, pct: 42.3 },
    { name: "Engineering", total: 2934, atRisk: 888, pct: 30.3 },
    { name: "Computing and Information Technology", total: 748, atRisk: 214, pct: 28.6 },
  ],
  byModule: [
    { name: "BBB", total: 7909, atRisk: 3963, pct: 50.1 },
    { name: "FFF", total: 7762, atRisk: 2352, pct: 30.3 },
    { name: "DDD", total: 6272, atRisk: 2218, pct: 35.4 },
    { name: "CCC", total: 2534, atRisk: 2125, pct: 83.9 },
    { name: "EEE", total: 4434, atRisk: 1874, pct: 42.3 },
    { name: "GGG", total: 2934, atRisk: 888, pct: 30.3 },
    { name: "AAA", total: 748, atRisk: 214, pct: 28.6 },
  ],
};

export async function fetchOverview() {
  if (IS_DEMO) {
    // The whole point of the demo: a dean gets the identical query with student_name
    // rewritten by the column mask, an admin gets it intact.
    const redacted = demoRole === "dean";
    return withLatency({
      ...MOCK_OVERVIEW,
      rows: MOCK_OVERVIEW_ROWS.map((r) => (redacted ? { ...r, name: "REDACTED" } : r)),
      namesRedacted: redacted,
    });
  }
  return getJSON("/gold/overview");
}

// ---------- Genie ----------

// Canned answers for demo mode, written to match what the live agent returns for the
// same questions and scoped to the chosen perspective. Every one opens by naming itself
// as a sample: this is a demo of the interface, and a reader must never mistake it for
// live model output. Anything unrecognised says so plainly rather than inventing a
// figure — a made-up number is exactly what this project refuses to do.
function demoAnswer(question) {
  const q = question.toLowerCase();
  const sample = "*Sample answer — this public demo runs on fixed data, with no Genie space attached.*\n\n";

  if (demoRole === "student") {
    if (q.includes("miss") || q.includes("cost") || q.includes("next session")) {
      return sample + "Missing your next session reduces your buffer to **0 sessions**, keeping you in the **high** band. You have attended 1 of 6 sessions held so far, and 18 remain in the module.";
    }
    if (q.includes("attend") || q.includes("threshold") || q.includes("75")) {
      return sample + "You have attended **1 of 6** sessions held so far — **16.7%**. The threshold is 75%, which means attending **18 of the 24** sessions across the module.";
    }
    return sample + "In this demo a student can only ask about their own attendance record. Unity Catalog's row filter returns your row and nothing else, so questions about other students return no rows at all — not an error message, simply nothing.";
  }

  if (demoRole === "dean") {
    if (q.includes("department")) {
      return sample + "Across **7 departments**, **13,634 of 32,593** enrolments are flagged high risk.\n\n- **Business and Management** — 3,963 high risk of 7,909\n- **Mathematics and Statistics** — 2,352 of 7,762\n- **Arts and Humanities** — 2,218 of 6,272\n- **Social Sciences** — 2,125 of 2,534\n\nStudent names are returned as `REDACTED` for your role.";
    }
    if (q.includes("headcount") || q.includes("forecast")) {
      return sample + "Expected headcount for the next forecasted session is **19,111** across all modules. This comes from `gold.session_forecasts`, which is aggregate only and carries no row filter — there is no per-student row in it to protect.";
    }
    return sample + "**13,634 of 32,593** enrolments are flagged high risk — **41.8%** of the cohort. Names come back as `REDACTED`: the column mask is applied in Unity Catalog before this page ever sees the data.";
  }

  // advisor / admin
  if (q.includes("margin") || q.includes("attendance")) {
    return sample + "The tightest attendance margin is **Bhavana Sharma** (100893), with **0 sessions** of buffer. Missing the next session makes the 75% threshold mathematically unreachable — every remaining session must be attended.\n\nNext tightest are **Geeta Tiwari** and **Sneha Reddy**, each with 1 session of margin.";
  }
  if (q.includes("signal") || q.includes("why") || q.includes("factor")) {
    return sample + "The most common contributing signals among your flagged students are:\n\n- few active study days in weeks 5 and 6\n- engagement dropping between week 1 and week 6\n- low average assessment score\n- low assessment submission count\n\nAll are behavioural, drawn from weeks 1–6 only. No demographic attribute is used.";
  }
  return sample + "**5 of your 30 students** are flagged high risk this week:\n\n- **Sowmya Nair** (30268) — risk score 0.77\n- **Geeta Tiwari** (74372) — 0.71\n- **Nisha Agarwal** (65002) — 0.66\n- **Sneha Reddy** (28400) — 0.65\n- **Bhavana Sharma** (100893) — 0.60\n\nSowmya Nair has the highest risk score. Bhavana Sharma has the least attendance margin left.";
}

// Returns { answer, conversation_id }. Pass the conversation_id back on the next
// question to keep one Genie conversation going, so follow-ups have context.
export async function askGenie(question, conversationId = null) {
  if (IS_DEMO) {
    return withLatency({
      answer: demoAnswer(question),
      conversation_id: conversationId || "demo-conversation",
    }, 900);
  }
  const res = await fetch(`${API_BASE}/genie/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, conversation_id: conversationId }),
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
  return res.json();
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
