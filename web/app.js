import {
  USE_LIVE_API, ROLE_LABEL, SUGGESTIONS, GOVERNANCE,
  fetchMe, fetchCohort, fetchStudentSelf, fetchOverview, askGenie,
} from "./api.js?v=3";

const root = document.getElementById("app");
const BAND_ORDER = ["low", "medium", "high", "unavoidable"];

const state = {
  page: "dashboard",          // "dashboard" | "how"
  me: null,                   // { email, role, advisor_id, student_id, department }
  meError: null,
  loading: true,

  cohort: null,
  cohortError: null,
  self: null,
  selfError: null,
  overview: null,
  overviewError: null,

  sel: 0,                     // selected student index (advisor)
  cohortExpanded: false,
  loggedActions: {},

  q: "",
  answer: "",
  answerError: null,
  asking: false,
};

function setState(patch) {
  Object.assign(state, patch);
  render();
}

// ---------- theme ----------
// No stored preference means "follow the OS", which the CSS handles via
// prefers-color-scheme. Only an explicit choice writes data-theme. Storage access can
// throw outright in some privacy modes, so every read/write is guarded.

function storedTheme() {
  try { return localStorage.getItem("amc-theme"); } catch (_) { return null; }
}

function activeTheme() {
  const stored = storedTheme();
  if (stored) return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function toggleTheme() {
  const next = activeTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("amc-theme", next); } catch (_) { /* not persisted */ }
  render();
}

// ---------- boot: identity first, then only what this role can read ----------

async function boot() {
  render();

  let me;
  try {
    me = await fetchMe();
  } catch (err) {
    state.meError = err.message;
    state.loading = false;
    return render();
  }

  state.me = me;
  state.loading = false;
  state.q = (SUGGESTIONS[me.role] || [])[0] || "";
  render();

  const role = me.role;
  if (!role) return;

  // Each panel loads and fails independently — one endpoint erroring must not blank
  // the rest of the dashboard.
  if (role === "advisor" || role === "admin") {
    fetchCohort()
      .then((cohort) => setState({ cohort }))
      .catch((err) => setState({ cohortError: err.message, cohort: [] }));
  }
  if (role === "student") {
    fetchStudentSelf()
      .then((self) => setState({ self }))
      .catch((err) => setState({ selfError: err.message }));
  }
  if (role === "dean" || role === "admin") {
    fetchOverview()
      .then((overview) => setState({ overview }))
      .catch((err) => setState({ overviewError: err.message }));
  }

  if (state.q) submitAsk(state.q);
}

// ---------- helpers ----------

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Genie answers come back as markdown (**bold**, "- " bullets, blank-line paragraphs).
// Escape first so nothing in the answer is ever treated as real HTML, then convert
// only that small subset — no parser library needed for output this simple.
function renderMarkdown(s) {
  const escaped = esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  let html = "";
  let inList = false;
  for (const line of escaped.split("\n")) {
    const bullet = /^\s*[-*]\s+(.*)/.exec(line);
    if (bullet) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${bullet[1]}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      if (line.trim()) html += `<p>${line}</p>`;
    }
  }
  if (inList) html += "</ul>";
  return html || `<p>${escaped}</p>`;
}

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function bandChip(band) {
  return `<span class="chip" data-band="${esc(band)}">${esc(band)}</span>`;
}

function pluralSessions(n) {
  return `${n} ${n === 1 ? "session" : "sessions"}`;
}

function errorBox(msg) {
  return `<div class="notice notice-error">${esc(msg)}</div>`;
}

// ---------- chrome ----------

function renderHeader() {
  const me = state.me;
  const roleLabel = me && me.role ? ROLE_LABEL[me.role] || me.role : null;
  const scope = me && (me.role === "advisor" ? me.advisor_id : me.role === "student" ? me.student_id : me.department);

  const identity = me && me.email
    ? `<span class="identity" title="Signed in as ${esc(me.email)}${scope ? ` · ${esc(scope)}` : ""}">
         <span class="identity-dot" data-role="${esc(me.role || "none")}"></span>
         <span class="identity-role">${esc(roleLabel || "no role")}</span>
         <span class="identity-email">${esc(me.email)}</span>
       </span>`
    : "";

  return `
  <header>
    <div class="brand" data-nav="dashboard">
      <span class="brand-name">Ask My Cohort</span>
    </div>
    <nav>
      <button class="nav-item ${state.page === "dashboard" ? "active" : ""}" data-nav="dashboard">Dashboard</button>
      <button class="nav-item ${state.page === "how" ? "active" : ""}" data-nav="how">How it works</button>
    </nav>
    <span class="spacer"></span>
    ${/* Live is the expected state and needs no badge. Demo mode always announces
          itself — mock data must never be mistakable for the real thing. */ ""}
    ${USE_LIVE_API ? "" : `<span class="source-chip demo"><span class="dot"></span>Demo data</span>`}
    <button class="icon-btn" data-theme-toggle title="Switch to ${activeTheme() === "dark" ? "light" : "dark"} mode"
            aria-label="Switch to ${activeTheme() === "dark" ? "light" : "dark"} mode">${activeTheme() === "dark" ? "☀" : "☾"}</button>
    ${identity}
  </header>`;
}

function renderFooter() {
  return `
  <footer>
    <span>Ask My Cohort · Databricks Campus Hackathon, BMSCE Edition</span>
    <span class="spacer"></span>
    <span>Attendance is synthetic and disclosed · OULAD, openly licensed, anonymised</span>
  </footer>`;
}

// ---------- the ask box (Genie is the product — present for every role) ----------

function renderAsk() {
  const suggestions = SUGGESTIONS[state.me?.role] || [];
  const chips = suggestions.map((t) => `<button class="suggestion" data-pick="${esc(t)}">${esc(t)}</button>`).join("");

  const body = state.asking
    ? `<div class="answer thinking"><span class="pulse"></span>Asking Genie…</div>`
    : state.answer
    ? `<div class="answer">${renderMarkdown(state.answer)}</div>`
    : state.answerError
    ? `<div class="answer"><span class="answer-error">Genie didn't answer: ${esc(state.answerError)}</span></div>`
    : "";

  return `
  <section class="ask-card">
    <div class="ask-row">
      <input class="ask-input" id="ask-input" type="text" value="${esc(state.q)}"
             placeholder="Ask in plain English…" autocomplete="off" />
      <button class="btn-primary" data-cta="ask" ${state.asking ? "disabled" : ""}>${state.asking ? "Asking…" : "Ask"}</button>
    </div>
    <div class="suggestions">${chips}</div>
    ${body}
  </section>`;
}

// ---------- hero ----------

function hero(title, sub) {
  return `
  <section class="hero">
    <p class="hero-greeting">${greeting()}</p>
    <h1 class="hero-title">${title}</h1>
    ${sub ? `<p class="hero-sub">${sub}</p>` : ""}
  </section>`;
}

// ---------- advisor ----------

function renderAdvisor() {
  const cohort = state.cohort;

  if (state.cohortError) return hero("Your cohort", "") + errorBox(`Couldn't load your cohort: ${state.cohortError}`);
  if (!cohort) return hero("Your cohort", "") + `<div class="notice">Loading your students…</div>`;

  const atRisk = cohort.filter((s) => s.atRisk);
  const tightest = [...cohort].sort((a, b) => (a.left ?? 99) - (b.left ?? 99))[0];

  const heroTitle = atRisk.length
    ? `<strong>${atRisk.length}</strong> of your ${cohort.length} students ${atRisk.length === 1 ? "is" : "are"} flagged high risk.`
    : `None of your ${cohort.length} students are flagged high risk this week.`;
  const heroSub = tightest
    ? `Tightest attendance margin: ${esc(tightest.name)} — ${pluralSessions(tightest.left ?? 0)} of buffer left.`
    : "";

  const sel = cohort[state.sel] || cohort[0];

  const cards = atRisk.length
    ? atRisk.map((s) => `
      <button class="student-card ${sel && s.i === sel.i ? "selected" : ""}" data-select="${s.i}">
        <div class="student-card-head">
          <div>
            <div class="student-name">${esc(s.name)}</div>
            <div class="student-id">${esc(s.id)} · ${esc(s.course)}</div>
          </div>
          ${bandChip(s.band)}
        </div>
        <div class="signals">${s.signals.slice(0, 3).map((sig) => `<span class="signal">${esc(sig)}</span>`).join("")}</div>
        <div class="student-card-foot">
          <span>${pluralSessions(s.left ?? 0)} of buffer</span>
          <span class="mono">${esc(s.pct)}% attended</span>
        </div>
      </button>`).join("")
    : `<div class="notice">No high-risk students right now.</div>`;

  return `
  ${hero(heroTitle, heroSub)}
  ${renderAsk()}

  <section class="split">
    <div>
      <h2 class="section-title">Flagged this week</h2>
      <p class="section-note">Ordered by risk score. "At risk" means <span class="mono">risk_band = 'high'</span> — the same definition the Genie agent uses.</p>
      <div class="student-grid">${cards}</div>

      <button class="disclosure-toggle" data-toggle="cohort">
        ${state.cohortExpanded ? "Hide" : "Show"} all ${cohort.length} students
        <span>${state.cohortExpanded ? "▲" : "▼"}</span>
      </button>
      ${state.cohortExpanded ? renderCohortTable(cohort) : ""}

      <p class="provenance">
        <span class="mono">row filter: campus.ops.rf_risk</span>
        ${cohort.length} ${cohort.length === 1 ? "row" : "rows"} visible · gold.risk_signals ⋈ gold.attendance_buffers
      </p>
    </div>

    ${sel ? renderBufferPanel(sel) : ""}
  </section>`;
}

function renderCohortTable(cohort) {
  return `
  <div class="table">
    <div class="table-head">
      <span>Student</span><span>Risk</span><span>Buffer</span><span class="right">Margin</span>
    </div>
    ${cohort.map((s) => `
      <button class="table-row" data-select="${s.i}">
        <span>
          <span class="student-name">${esc(s.name)}</span>
          <span class="student-id">${esc(s.id)}</span>
        </span>
        <span class="band-text" data-band="${esc(s.riskBand)}">${esc(s.riskBand)}</span>
        <span class="band-text" data-band="${esc(s.band)}">${esc(s.band)}</span>
        <span class="right mono">${s.left}</span>
      </button>`).join("")}
  </div>`;
}

function renderBufferPanel(sel) {
  const rows = [
    ["Sessions held", String(sel.held)],
    ["Attended", `${sel.att} (${sel.pct}%)`],
    ["Remaining", String(sel.rem)],
    ["To clear 75%", `needs ${sel.needed} of ${sel.total}`],
    ["Can still miss", sel.reachable ? pluralSessions(sel.left ?? 0) : "threshold unreachable"],
  ];

  const actions = [
    ["log", `Log a conversation with ${esc(sel.name.split(" ")[0])}`],
    ["share", "Share buffer summary with the student"],
    ["dismiss", "Dismiss flag & record reason"],
  ];

  return `
  <aside class="rail">
    <div class="panel">
      <p class="panel-label">Buffer detail · arithmetic</p>
      <h3 class="panel-title">${esc(sel.name)}</h3>
      <p class="panel-meta mono">${esc(sel.id)} · ${esc(sel.course)}</p>

      <div class="band-track">
        ${BAND_ORDER.map((b) => `
          <div class="band-seg ${b === sel.band ? "on" : ""}" data-band="${b}">
            <span class="band-bar"></span>
            <span class="band-label">${b}</span>
          </div>`).join("")}
      </div>

      <div class="cost">${esc(sel.cost)}</div>

      <dl class="stats">
        ${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd class="mono">${esc(v)}</dd></div>`).join("")}
      </dl>
      <p class="panel-note">Pure arithmetic over attendance records — no model, no prediction. Attendance data is synthetic and disclosed.</p>
    </div>

    <div class="panel">
      <p class="panel-label">This week's intervention</p>
      <div class="actions">
        ${actions.map(([key, label]) => {
          const done = state.loggedActions[`${sel.id}:${key}`];
          return `<button class="action" data-action="${key}" data-action-id="${sel.id}">
                    <span>${label}</span>${done ? `<span class="done">Logged ✓</span>` : ""}
                  </button>`;
        }).join("")}
      </div>
      <p class="panel-note">Every action is logged against the flag — without the outcome loop the system only predicts.</p>
    </div>
  </aside>`;
}

// ---------- student ----------

function renderStudent() {
  if (state.selfError) return hero("Your attendance", "") + errorBox(`Couldn't load your record: ${state.selfError}`);
  const me = state.self;
  if (!me) return hero("Your attendance", "") + `<div class="notice">Loading your record…</div>`;

  // Forward-looking what-ifs, computed the same way for each step. Deliberately uses
  // relative labels, not calendar dates: session_date in gold is a day-offset integer,
  // so inventing "Thu 4 Sep" would be fiction.
  const steps = ["your next session", "the one after that", "the third from now", "the fourth from now"];
  const whatIf = steps.map((label, k) => {
    const left = (me.left ?? 0) - (k + 1);
    return { label, left, reachable: left >= 0 };
  });

  return `
  ${hero(`Your attendance buffer is <strong class="band-text" data-band="${esc(me.band)}">${esc(me.band)}</strong>.`,
         `${esc(me.name)} · ${esc(me.id)} · ${esc(me.course)}`)}

  <section class="buffer-hero">
    <div class="buffer-figures">
      <div>
        <p class="panel-label">Attended</p>
        <p class="big mono">${esc(me.pct)}%</p>
        <p class="panel-note">${me.att} of ${me.held} sessions held</p>
      </div>
      <div>
        <p class="panel-label">Threshold</p>
        <p class="big mono">${me.thresholdPct}%</p>
        <p class="panel-note">needs ${me.needed} of ${me.total} across the module</p>
      </div>
      <div>
        <p class="panel-label">Sessions remaining</p>
        <p class="big mono">${me.rem}</p>
        <p class="panel-note">still to be held</p>
      </div>
    </div>

    <div class="meter" data-band="${esc(me.band)}">
      <span class="meter-fill" style="width:${Math.min(100, Number(me.pct))}%"></span>
      <span class="meter-mark" style="left:${me.thresholdPct}%"></span>
    </div>
    <p class="meter-legend"><span>0%</span><span>threshold ${me.thresholdPct}%</span><span>100%</span></p>

    <div class="cost cost-lg">${esc(me.cost)}</div>
    <p class="panel-note">We show the cost of missing a session, never an allowance to spend.</p>
  </section>

  ${renderAsk()}

  <section class="split-even">
    <div class="panel">
      <p class="panel-label">If you miss the next ones in turn</p>
      <div class="whatif">
        ${whatIf.map((w) => `
          <div class="whatif-row">
            <span>Miss ${w.label}</span>
            <span class="band-text" data-band="${w.reachable ? "high" : "unavoidable"}">
              ${w.reachable ? `${pluralSessions(w.left)} of buffer left` : "75% becomes unreachable"}
            </span>
          </div>`).join("")}
      </div>
    </div>

    <div class="panel">
      <p class="panel-label">Why you don't see a risk score</p>
      <p class="panel-body">Risk flags are not shown to students, by design. They go to your advisor so they can offer support — never into a ranking, and never as an automated decision about your grade or enrolment.</p>
      <p class="provenance"><span class="mono">row filter: campus.ops.rf_risk</span> returns your row and nothing else — enforced in the catalog, not in this page.</p>
    </div>
  </section>`;
}

// ---------- dean / admin ----------

function renderOverviewBody(role) {
  if (state.overviewError) return hero("Cohort overview", "") + errorBox(`Couldn't load the overview: ${state.overviewError}`);
  const o = state.overview;
  if (!o) return hero("Cohort overview", "") + `<div class="notice">Loading the cohort…</div>`;

  const heroTitle = `<strong>${o.atRisk}</strong> of ${o.total} enrolments ${o.atRisk === 1 ? "is" : "are"} flagged high risk.`;
  const heroSub = o.namesRedacted
    ? "Student names arrive as <span class='mono'>'REDACTED'</span> — the column mask is applied in Unity Catalog, before this page sees the data."
    : "Names are visible to your role.";

  const tiles = [
    { k: "At-risk share", v: `${o.atRiskPct.toFixed(1)}%`, note: `${o.atRisk} of ${o.total}, weeks 1–6` },
    { k: "Enrolments visible", v: String(o.total), note: `${o.modules} module${o.modules === 1 ? "" : "s"} · ${o.departments} department${o.departments === 1 ? "" : "s"}` },
    { k: "Names visible", v: o.namesRedacted ? "0" : String(o.total), note: o.namesRedacted ? "column mask applied" : "your role sees names" },
    { k: "Next session headcount", v: o.nextHeadcount != null ? String(o.nextHeadcount) : "—", note: o.nextSessionDate != null ? `forecast for day ${o.nextSessionDate}, aggregate only` : "aggregate only" },
  ];

  return `
  ${hero(heroTitle, heroSub)}
  ${renderAsk()}

  <section class="tiles">
    ${tiles.map((t) => `
      <div class="tile">
        <p class="panel-label">${esc(t.k)}</p>
        <p class="tile-value">${esc(t.v)}</p>
        <p class="panel-note">${t.note}</p>
      </div>`).join("")}
  </section>

  <section class="split-even">
    ${bandPanel("Risk bands", o.riskBands, o.total)}
    ${bandPanel("Attendance buffer bands", o.bufferBands, o.total)}
  </section>

  <section class="panel">
    <p class="panel-label">Highest risk scores${o.namesRedacted ? " · names masked in the catalog" : ""}</p>
    <div class="table">
      <div class="table-head"><span>Student</span><span>Name</span><span>Module</span><span class="right">Risk</span></div>
      ${o.rows.map((r) => `
        <div class="table-row static">
          <span class="mono">${esc(r.id)}</span>
          <span class="${(r.name || "") === "REDACTED" ? "redacted" : ""}">${esc(r.name)}</span>
          <span>${esc(r.course)}</span>
          <span class="right band-text" data-band="${esc(r.riskBand)}">${esc(r.riskBand)}</span>
        </div>`).join("")}
    </div>
    <p class="provenance">
      <span class="mono">row filter: campus.ops.rf_risk</span>
      ${role === "dean"
        ? "A dean sees every row institution-wide; the distinction from an advisor is the name mask, not the row count."
        : "Admin sees every row with names intact."}
    </p>
  </section>`;
}

function bandPanel(title, bands, total) {
  const byBand = Object.fromEntries(bands.map((b) => [b.band, b.n]));
  return `
  <div class="panel">
    <p class="panel-label">${esc(title)}</p>
    <div class="bars">
      ${BAND_ORDER.map((b) => {
        const n = byBand[b] || 0;
        const pct = total ? (100 * n / total) : 0;
        return `
        <div class="bar-row ${n ? "" : "empty"}" data-band="${b}">
          <span class="bar-label">${b}</span>
          <span class="bar-track"><span class="bar-fill" style="width:${pct}%"></span></span>
          <span class="bar-n mono">${n}</span>
        </div>`;
      }).join("")}
    </div>
    <p class="panel-note">${total} rows visible to you.</p>
  </div>`;
}

// ---------- unmapped account ----------

function renderUnmapped() {
  const email = state.me?.email;
  return `
  ${hero("Your account isn't mapped to a role yet.", "")}
  <section class="panel prose">
    <p class="panel-body">
      ${email ? `<span class="mono">${esc(email)}</span> has` : "This account has"} no row in
      <span class="mono">campus.ops.role_map</span>, so Unity Catalog's row filter returns
      <strong>zero rows</strong> from every governed table. Nothing is broken — this is exactly
      what the governance layer is supposed to do with an unknown identity.
    </p>
    <p class="panel-body">An admin can grant access by adding one row:</p>
    <pre class="code">INSERT INTO campus.ops.role_map
  (user_email, role, advisor_id, student_id, department)
VALUES ('${esc(email || "you@example.com")}', 'advisor', 'ADV001', NULL, 'Computing and Information Technology');</pre>
    <p class="panel-note">Roles: advisor · dean · student · admin. A student row needs <span class="mono">student_id</span>; an advisor row needs <span class="mono">advisor_id</span>. Leaving them NULL matches no rows.</p>
  </section>`;
}

// ---------- how it works ----------

function renderHow() {
  const g = GOVERNANCE;
  return `
  ${hero("How it works", "One governed set of tables. The same question returns a different answer depending on who asks — enforced in Unity Catalog, beneath the agent.")}

  <section class="panel">
    <p class="panel-label">Medallion lineage</p>
    <div class="lineage">
      ${g.lineage.map((l) => `
        <div class="lineage-row">
          <span class="lineage-layer mono">${esc(l.layer)}</span>
          <span><span class="lineage-what">${esc(l.what)}</span><span class="panel-note">${esc(l.note)}</span></span>
        </div>`).join("")}
    </div>
  </section>

  <section class="panel">
    <p class="panel-label">What each role actually sees</p>
    <div class="table">
      <div class="table-head"><span>Role</span><span>Rows returned</span><span>student_name</span></div>
      ${g.effect.map((e) => `
        <div class="table-row static">
          <span>${esc(e.role)}</span>
          <span>${esc(e.rows)}</span>
          <span class="mono">${esc(e.names)}</span>
        </div>`).join("")}
    </div>
    <p class="panel-note">Note the dean row: dean is not department-scoped. It sees every row and differs from admin only by the name mask.</p>
  </section>

  ${g.policies.map((p) => `
    <section class="panel">
      <p class="panel-label">${esc(p.kind)} · <span class="mono">${esc(p.name)}</span></p>
      <p class="panel-note">Attached to ${esc(p.on)}</p>
      <pre class="code">${esc(p.sql)}</pre>
    </section>`).join("")}

  <section class="panel">
    <p class="panel-label">Stated plainly</p>
    <p class="panel-body">Attendance is <strong>synthetic and disclosed</strong>, generated from a causal story over real engagement. The risk model trains on real OULAD outcomes — weeks 1–6 only, behavioural features only, no demographic column in any feature set. We quote no accuracy figure: a number from a nine-hour build on partly synthetic data would mislead.</p>
  </section>

  <section class="panel">
    <p class="panel-label">Deliberately not built</p>
    ${g.notBuilt.map((n) => `
      <div class="notbuilt">
        <span class="notbuilt-what">${esc(n.what)}</span>
        <span class="panel-note">${esc(n.why)}</span>
      </div>`).join("")}
  </section>`;
}

// ---------- render ----------

function renderBody() {
  if (state.loading) return `<div class="notice">Resolving your identity…</div>`;
  if (state.meError) return errorBox(`Couldn't resolve who you are: ${state.meError}`);
  if (state.page === "how") return renderHow();

  const role = state.me?.role;
  if (!role) return renderUnmapped();
  if (role === "advisor") return renderAdvisor();
  if (role === "student") return renderStudent();
  if (role === "dean" || role === "admin") return renderOverviewBody(role);
  return errorBox(`Unrecognised role "${role}" in campus.ops.role_map.`);
}

function render() {
  root.innerHTML = `${renderHeader()}<main>${renderBody()}</main>${renderFooter()}`;

  const input = document.getElementById("ask-input");
  if (input) {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitAsk(input.value);
    });
  }
}

async function submitAsk(question) {
  const q = (question ?? state.q).trim();
  if (!q || state.asking) return;
  state.q = q;
  state.asking = true;
  state.answerError = null;
  render();
  // try/finally matters: without it a rejection would skip asking=false and leave the
  // button stuck on "Asking…" with no way to retry.
  try {
    state.answer = await askGenie(q);
  } catch (err) {
    state.answer = "";
    state.answerError = err.message;
  } finally {
    state.asking = false;
    render();
  }
}

root.addEventListener("click", (e) => {
  if (e.target.closest("[data-theme-toggle]")) return toggleTheme();

  const navEl = e.target.closest("[data-nav]");
  if (navEl) return setState({ page: navEl.dataset.nav });

  const pickEl = e.target.closest("[data-pick]");
  if (pickEl) return submitAsk(pickEl.dataset.pick);

  const selectEl = e.target.closest("[data-select]");
  if (selectEl) return setState({ sel: Number(selectEl.dataset.select) });

  const toggleEl = e.target.closest("[data-toggle='cohort']");
  if (toggleEl) return setState({ cohortExpanded: !state.cohortExpanded });

  const ctaEl = e.target.closest("[data-cta='ask']");
  if (ctaEl) {
    const el = document.getElementById("ask-input");
    return submitAsk(el ? el.value : state.q);
  }

  const actionEl = e.target.closest("[data-action]");
  if (actionEl) {
    const key = `${actionEl.dataset.actionId}:${actionEl.dataset.action}`;
    return setState({ loggedActions: { ...state.loggedActions, [key]: true } });
  }
});

boot();
