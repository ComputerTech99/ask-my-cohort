import {
  USE_LIVE_API, IS_DEMO, ROLE_LABEL, SUGGESTIONS, GOVERNANCE, DEMO_ROLES,
  getDemoRole, setDemoRole,
  fetchMe, fetchCohort, fetchStudentSelf, fetchOverview, askGenie,
} from "./api.js?v=11";

const root = document.getElementById("app");
const BAND_ORDER = ["low", "medium", "high", "unavoidable"];

const state = {
  page: "landing",            // "landing" | "chat" | "dashboard" | "how"
  me: null,                   // { email, role, advisor_id, student_id, department }
  meError: null,
  loading: true,

  // Chat thread. messages is [{ from: "you" | "genie", text }]; conversationId keeps
  // Genie's own conversation alive so follow-up questions have context.
  messages: [],
  conversationId: null,

  cohort: null,
  cohortError: null,
  self: null,
  selfError: null,
  overview: null,
  overviewError: null,

  sel: 0,                     // selected student index (advisor)
  cohortExpanded: false,
  loggedActions: {},

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

// ---------- demo perspective switching ----------

// Choosing a perspective re-runs the whole boot, so each role loads exactly the data
// its own role is allowed to read — the same path live mode takes after login.
function enterAsRole(role) {
  setDemoRole(role);
  resetRoleState();
  state.page = "dashboard";
  boot();
}

function leaveDemoRole() {
  setDemoRole(null);
  resetRoleState();
  state.page = "dashboard";
  render();
}

function resetRoleState() {
  Object.assign(state, {
    me: null, meError: null, loading: true,
    cohort: null, cohortError: null,
    self: null, selfError: null,
    overview: null, overviewError: null,
    sel: 0, cohortExpanded: false, loggedActions: {},
    messages: [], conversationId: null, asking: false,
  });
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
  render();

  // null in demo mode until a perspective is chosen; in live mode it means the account
  // has no role_map row, which renderUnmapped() explains.
  const role = me?.role;
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

// Thousands separators — the cohort is 32k rows, and "32593" is unreadable at a glance.
function num(n) {
  return Number(n ?? 0).toLocaleString();
}

// Severity tone for a percentage, so bars and tiles pick up meaningful colour rather
// than decorative colour.
function toneFor(pct) {
  if (pct >= 35) return "unavoidable";
  if (pct >= 20) return "high";
  if (pct >= 10) return "medium";
  return "low";
}

// One bar per row, coloured by how bad its share is.
function breakdownPanel(label, rows) {
  if (!rows || !rows.length) return "";
  const max = Math.max(...rows.map((r) => r.atRisk), 1);
  return `
  <div class="panel">
    <p class="panel-label">${esc(label)}</p>
    <div class="bars">
      ${rows.map((r) => `
        <div class="bar-row wide" data-band="${toneFor(r.pct)}">
          <span class="bar-label-name" title="${esc(r.name)}">${esc(r.name)}</span>
          <span class="bar-track"><span class="bar-fill" style="width:${(100 * r.atRisk / max).toFixed(1)}%"></span></span>
          <span class="bar-n mono">${num(r.atRisk)}</span>
          <span class="bar-pct mono">${r.pct.toFixed(0)}%</span>
        </div>`).join("")}
    </div>
    <p class="panel-note">Bar length is the count of high-risk students; the percentage is their share of that group.</p>
  </div>`;
}

// One stacked bar showing the whole cohort's risk mix at a glance.
function stackedBar(bands, total) {
  if (!total) return "";
  const byBand = Object.fromEntries(bands.map((b) => [b.band, b.n]));
  const order = ["low", "medium", "high", "unavoidable"];
  return `
  <div class="stack">
    <div class="stack-bar">
      ${order.map((b) => {
        const n = byBand[b] || 0;
        if (!n) return "";
        return `<span class="stack-seg" data-band="${b}" style="width:${(100 * n / total).toFixed(2)}%" title="${b}: ${num(n)}"></span>`;
      }).join("")}
    </div>
    <div class="stack-key">
      ${order.filter((b) => byBand[b]).map((b) => `
        <span class="key" data-band="${b}">
          <span class="key-dot"></span>${b}
          <strong class="mono">${num(byBand[b])}</strong>
          <span class="key-pct">${(100 * byBand[b] / total).toFixed(0)}%</span>
        </span>`).join("")}
    </div>
  </div>`;
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
    <div class="brand" data-nav="landing" role="button" tabindex="0" aria-label="Back to the start page"
         title="Back to the start page">
      <span class="brand-name">Ask My Cohort</span>
    </div>
    <nav>
      <button class="nav-item ${state.page === "chat" ? "active" : ""}" data-nav="chat">Agent</button>
      <button class="nav-item ${state.page === "dashboard" ? "active" : ""}" data-nav="dashboard">Dashboard</button>
      <button class="nav-item ${state.page === "how" ? "active" : ""}" data-nav="how">How it works</button>
    </nav>
    <span class="spacer"></span>
    ${/* Live is the expected state and needs no badge. Demo mode always announces
          itself — mock data must never be mistakable for the real thing. */ ""}
    ${USE_LIVE_API ? "" : `<button class="source-chip demo" data-nav="pick" title="Switch perspective">
        <span class="dot"></span>Demo${me && me.role ? ` · viewing as ${esc(ROLE_LABEL[me.role] || me.role)}` : ""}
        <span class="switch-hint">switch</span>
      </button>`}
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

// A short prompt to jump into the conversation, shown at the foot of data views.
function renderAskTeaser() {
  const suggestions = (SUGGESTIONS[state.me?.role] || []).slice(0, 3);
  return chapter({
    eyebrow: "Ask",
    title: "Or just ask.",
    note: "The agent answers over these same governed tables — it can only tell you what your role is allowed to see.",
    body: `
      <div class="teaser">
        <button class="btn-primary" data-nav="chat">Open the agent →</button>
        <div class="suggestions">
          ${suggestions.map((t) => `<button class="suggestion" data-ask="${esc(t)}">${esc(t)}</button>`).join("")}
        </div>
      </div>`,
  });
}

// ---------- chat ----------

function renderChat() {
  const role = state.me?.role;
  const suggestions = SUGGESTIONS[role] || [];
  const empty = state.messages.length === 0;

  const thread = empty
    ? `<div class="chat-empty">
         <h1 class="chat-empty-title">What do you want to know?</h1>
         <p class="chat-empty-sub">Ask about your students in plain English. Every answer is scoped to what your role can see — the catalog decides that, not this page.</p>
         <div class="starters">
           ${suggestions.map((t) => `<button class="starter" data-ask="${esc(t)}">${esc(t)}<span>→</span></button>`).join("")}
         </div>
       </div>`
    : state.messages.map((m) => m.from === "you"
        ? `<div class="msg you"><div class="bubble">${esc(m.text)}</div></div>`
        : `<div class="msg genie">
             <span class="msg-avatar" aria-hidden="true"></span>
             <div class="msg-body ${m.error ? "is-error" : ""}">${m.error ? esc(m.text) : renderMarkdown(m.text)}</div>
           </div>`
      ).join("") + (state.asking
        ? `<div class="msg genie">
             <span class="msg-avatar" aria-hidden="true"></span>
             <div class="msg-body"><span class="typing"><i></i><i></i><i></i></span></div>
           </div>`
        : "");

  return `
  <div class="chat">
    <div class="chat-thread" id="chat-thread">
      <div class="chat-thread-inner">${thread}</div>
    </div>
    <div class="chat-composer">
      <div class="composer-inner">
        <div class="composer-box">
          <input class="composer-input" id="ask-input" type="text"
                 placeholder="Ask about your cohort…" autocomplete="off"
                 ${state.asking ? "disabled" : ""} />
          <button class="composer-send" data-cta="ask" ${state.asking ? "disabled" : ""} aria-label="Send">↑</button>
        </div>
        <p class="composer-note">
          ${state.conversationId ? "Follow-up questions keep the thread's context. " : ""}Answers come from a Databricks Genie agent over governed tables${role ? ` · answering as <strong>${esc(ROLE_LABEL[role] || role)}</strong>` : ""}.
        </p>
      </div>
    </div>
  </div>`;
}

// ---------- perspective chooser (demo only) ----------

const DEMO_ROLE_COPY = {
  advisor: {
    tone: "low",
    who: "Dr. Meera Rao",
    sees: "Their own 30 students, by name, with contributing signals and attendance buffer.",
    cannot: "Cannot see any other advisor's students.",
  },
  dean: {
    tone: "medium",
    who: "Dean, School of Engineering",
    sees: "Every one of the 32,593 enrolments — but every student_name comes back REDACTED.",
    cannot: "Cannot identify an individual student.",
  },
  student: {
    tone: "info",
    who: "Sneha Reddy",
    sees: "Their own attendance buffer and what missing the next session costs.",
    cannot: "Never shown a risk score — by design, not by omission.",
  },
  admin: {
    tone: "unavoidable",
    who: "Catalog admin",
    sees: "Every row with names intact, plus the governance console showing the live policy SQL.",
    cannot: "Sees everything — which is exactly why the role exists in role_map.",
  },
};

function renderPicker() {
  return hero({
    eyebrow: "Public demo",
    headline: "Whose view do you want to see?",
    sub: "In the real deployment you don't get this choice — Unity Catalog resolves your role from your login and returns only what it permits. This demo lets you stand in each role in turn, so you can watch the same data change shape.",
  }) + chapter({
    body: `
      <div class="picker">
        ${DEMO_ROLES.map((r) => {
          const c = DEMO_ROLE_COPY[r];
          return `
          <button class="pick-card" data-pick-role="${r}" data-band="${c.tone}">
            <span class="pick-role">${esc(ROLE_LABEL[r])}</span>
            <span class="pick-who">${esc(c.who)}</span>
            <span class="pick-sees">${esc(c.sees)}</span>
            <span class="pick-cannot">${esc(c.cannot)}</span>
            <span class="pick-go">Enter as ${esc(ROLE_LABEL[r].toLowerCase())} →</span>
          </button>`;
        }).join("")}
      </div>
      <p class="provenance">
        <span class="mono">campus.ops.rf_risk</span>
        <span class="mono">campus.ops.mask_name</span>
        In the live system these two functions produce the differences above. Here they are reproduced with fixed sample data.
      </p>`,
  });
}

// ---------- landing ----------

// A miniature of the agent's own empty state. Deliberately shows the composer and
// starter prompts rather than a sample answer — inventing a plausible-looking Genie
// reply on the front page would be fabricating model output.
function agentPreview() {
  return `
  <div class="agent-preview" data-nav="chat" role="button" tabindex="0" aria-label="Open the agent">
    <div class="ap-bar">
      <span class="ap-dot"></span><span class="ap-dot"></span><span class="ap-dot"></span>
      <span class="ap-title">Ask My Cohort · agent</span>
    </div>
    <div class="ap-body">
      <p class="ap-heading">What do you want to know?</p>
      <div class="ap-starters">
        <span class="ap-starter">Which of my students are at risk this week?<i>→</i></span>
        <span class="ap-starter">Who has the least attendance margin left?<i>→</i></span>
      </div>
      <div class="ap-composer">
        <span class="ap-placeholder">Ask about your cohort…</span>
        <span class="ap-send">↑</span>
      </div>
    </div>
  </div>`;
}

function renderLanding() {
  return hero({
    eyebrow: "Genie-powered campus intelligence",
    headline: `Which of my students are quietly falling behind<span class="q">?</span>`,
    sub: "Today that answer means joining attendance, LMS activity and assessment records across three systems a faculty advisor cannot query. So nobody asks, and the answer arrives in week fourteen with the results.",
    aside: agentPreview(),
  }) + chapter({
    tight: true,
    body: `
      <div class="cta-row">
        <button class="btn-primary btn-lg" data-nav="chat">Open the agent →</button>
        <button class="btn-ghost" data-nav="dashboard">View your data</button>
      </div>`,
  }) + chapter({
    eyebrow: "What it answers",
    title: "Three questions, one governed source.",
    body: `
      <div class="tiles tiles-3">
        <div class="tile">
          <p class="panel-label">gold.risk_signals</p>
          <p class="tile-head">Who is trending toward failing</p>
          <p class="panel-note">Plus the contributing factors. First six weeks of behaviour only — no demographics in any feature set.</p>
        </div>
        <div class="tile">
          <p class="panel-label">gold.attendance_buffers</p>
          <p class="tile-head">What missing tomorrow costs</p>
          <p class="panel-note">Pure arithmetic, no model. Four bands: low, medium, high, unavoidable. It cannot be wrong.</p>
        </div>
        <div class="tile">
          <p class="panel-label">gold.session_forecasts</p>
          <p class="tile-head">Expected headcount next session</p>
          <p class="panel-note">Aggregate, never per-student. Room planning and session design, not surveillance.</p>
        </div>
      </div>`,
  }) + chapter({
    eyebrow: "The strongest point",
    title: "The role hierarchy looks like frontend work.<br>It isn't.",
    note: "One governed set of tables with a row filter and a column mask enforced in Unity Catalog, beneath the agent — so it cannot be bypassed by querying the tables directly. The same English question returns a different answer depending on who asks.",
    body: `
      <div class="role-rows">
        <div class="role-row"><span class="role-name">Advisor</span><span>Their own students, <em>by name</em>, with signals and buffer.</span></div>
        <div class="role-row"><span class="role-name">Dean</span><span>Identical question, every row — but names come back <span class="mono">'REDACTED'</span>.</span></div>
        <div class="role-row"><span class="role-name">Student</span><span>Their own row only. Buffer arithmetic — never a risk flag.</span></div>
      </div>
      <p class="provenance"><span class="mono">campus.ops.rf_risk</span> <span class="mono">campus.ops.mask_name</span> — read them in full under How it works.</p>`,
  }) + chapter({
    eyebrow: "Stated plainly",
    title: "Where the data comes from.",
    body: `
      <div class="panel prose">
        <p class="panel-body">Attendance is <strong>synthetic and disclosed</strong>, generated from a causal story over real engagement. The risk model trains on real OULAD outcomes — weeks 1–6 only, behavioural features only, no demographic column in any feature set.</p>
        <p class="panel-body">No accuracy figure is quoted: a number from a nine-hour build on partly synthetic data would mislead. Flags reach an advisor for intervention — never a ranking, never the student, never an automated decision on a grade or enrolment.</p>
      </div>`,
  });
}

// ---------- hero ----------

// Editorial hero: one enormous number carrying the whole message, a short headline
// under it, supporting detail below. `stat` is optional — pages without a single
// meaningful figure (How it works, error states) just get the headline.
function hero({ eyebrow, stat, statBand, statAccent, headline, sub, aside }) {
  const bandAttr = statBand ? ` data-band="${esc(statBand)}"` : "";
  return `
  <section class="chapter hero reveal">
    <div class="wrap ${aside ? "hero-grid" : ""}">
      <div>
        <p class="eyebrow">${esc(eyebrow || greeting())}</p>
        ${stat ? `<p class="hero-stat${statAccent ? " accent" : ""}${String(stat).length > 4 ? " long" : ""}"${bandAttr}>${stat}</p>` : ""}
        <h1 class="hero-headline">${headline}</h1>
        ${sub ? `<p class="hero-sub">${sub}</p>` : ""}
      </div>
      ${aside ? `<div class="hero-aside">${aside}</div>` : ""}
    </div>
  </section>`;
}

// SVG ring chart. Segments are drawn as dash offsets around one circle, rotated so the
// first slice starts at 12 o'clock.
function donut(segments, centreValue, centreLabel) {
  const total = segments.reduce((a, s) => a + (s.n || 0), 0);
  if (!total) return "";
  const R = 56, CIRC = 2 * Math.PI * R;
  let offset = 0;
  const arcs = segments.filter((s) => s.n).map((s) => {
    const len = (s.n / total) * CIRC;
    const arc = `<circle class="donut-seg" data-band="${esc(s.band)}" r="${R}" cx="80" cy="80"
                   stroke-dasharray="${len.toFixed(2)} ${(CIRC - len).toFixed(2)}"
                   stroke-dashoffset="${(-offset).toFixed(2)}"><title>${esc(s.band)}: ${num(s.n)}</title></circle>`;
    offset += len;
    return arc;
  }).join("");

  return `
  <div class="donut-wrap">
    <svg class="donut" viewBox="0 0 160 160" role="img" aria-label="${esc(centreLabel)}">
      <g transform="rotate(-90 80 80)">
        <circle class="donut-track" r="${R}" cx="80" cy="80"></circle>
        ${arcs}
      </g>
    </svg>
    <div class="donut-centre">
      <span class="donut-value">${centreValue}</span>
      <span class="donut-label">${esc(centreLabel)}</span>
    </div>
  </div>
  <div class="donut-key">
    ${segments.filter((s) => s.n).map((s) => `
      <span class="key" data-band="${esc(s.band)}">
        <span class="key-dot"></span>${esc(s.band)}
        <strong class="mono">${num(s.n)}</strong>
      </span>`).join("")}
  </div>`;
}

// A titled band of content. Everything below the hero is one of these, so the page
// reads as a sequence of deliberate sections rather than a wall of cards.
function chapter({ eyebrow, title, note, body, tight, tint }) {
  return `
  <section class="chapter${tight ? " tight" : ""}${tint ? " tint" : ""} reveal">
    <div class="wrap">
      ${eyebrow ? `<p class="eyebrow">${esc(eyebrow)}</p>` : ""}
      ${title ? `<h2 class="chapter-title">${title}</h2>` : ""}
      ${note ? `<p class="chapter-note">${note}</p>` : ""}
      ${body}
    </div>
  </section>`;
}

// ---------- advisor ----------

function renderAdvisor() {
  const cohort = state.cohort;

  if (state.cohortError) {
    return hero({ headline: "Your cohort" }) + chapter({ body: errorBox(`Couldn't load your cohort: ${state.cohortError}`) });
  }
  if (!cohort) {
    return hero({ headline: "Your cohort" }) + chapter({ body: `<div class="notice">Loading your students…</div>` });
  }

  const atRisk = cohort.filter((s) => s.atRisk);
  const tightest = [...cohort].sort((a, b) => (a.left ?? 99) - (b.left ?? 99))[0];

  const heroBlock = hero({
    stat: atRisk.length ? String(atRisk.length) : "0",
    statAccent: atRisk.length > 0,
    headline: atRisk.length
      ? `of your ${cohort.length} students ${atRisk.length === 1 ? "is" : "are"} flagged high risk this week.`
      : `of your ${cohort.length} students are flagged high risk this week.`,
    sub: tightest
      ? `The tightest attendance margin belongs to ${esc(tightest.name)}. ${esc(tightest.cost)}`
      : "",
    aside: donut(
      BAND_ORDER.map((b) => ({ band: b, n: cohort.filter((s) => s.band === b).length })),
      String(cohort.length),
      "students",
    ),
  });

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

  return heroBlock + chapter({
    eyebrow: "Flagged this week",
    title: atRisk.length === 1 ? "The one worth a conversation." : `The ${atRisk.length} worth a conversation.`,
    note: `Ordered by risk score. "At risk" means <span class="mono">risk_band = 'high'</span> — the same definition the Genie agent uses, so this number and the agent's answer can never disagree.`,
    body: `
      <div class="split">
        <div>
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
      </div>`,
  }) + renderAskTeaser();
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
  if (state.selfError) {
    return hero({ headline: "Your attendance" }) + chapter({ body: errorBox(`Couldn't load your record: ${state.selfError}`) });
  }
  const me = state.self;
  if (!me) {
    return hero({ headline: "Your attendance" }) + chapter({ body: `<div class="notice">Loading your record…</div>` });
  }

  // Forward-looking what-ifs, computed the same way for each step. Deliberately uses
  // relative labels, not calendar dates: session_date in gold is a day-offset integer,
  // so inventing "Thu 4 Sep" would be fiction.
  const steps = ["your next session", "the one after that", "the third from now", "the fourth from now"];
  const whatIf = steps.map((label, k) => {
    const left = (me.left ?? 0) - (k + 1);
    return { label, left, reachable: left >= 0 };
  });

  return hero({
    eyebrow: "Your attendance buffer",
    stat: esc(me.band),
    statBand: me.band,
    headline: "is where your attendance stands right now.",
    // The cost sentence leads, never a count of sessions you may still skip.
    sub: esc(me.cost),
    // Attended vs missed so far — the same arithmetic the band comes from.
    aside: donut(
      [{ band: me.band, n: me.att }, { band: "unavoidable", n: Math.max(0, me.held - me.att) }],
      `${Math.round(Number(me.pct))}<span class="pc">%</span>`,
      "attended",
    ),
  }) + chapter({
    eyebrow: `${esc(me.name)} · ${esc(me.id)} · ${esc(me.course)}`,
    title: "The arithmetic, in full.",
    note: "No model and no prediction here — this is division. Attendance data is synthetic and disclosed.",
    body: `
      <div class="panel">
        <div class="buffer-figures">
          <div>
            <p class="panel-label">Attended</p>
            <p class="figure-value">${esc(me.pct)}%</p>
            <p class="panel-note">${me.att} of ${me.held} sessions held</p>
          </div>
          <div>
            <p class="panel-label">Threshold</p>
            <p class="figure-value">${me.thresholdPct}%</p>
            <p class="panel-note">needs ${me.needed} of ${me.total} across the module</p>
          </div>
          <div>
            <p class="panel-label">Sessions remaining</p>
            <p class="figure-value">${me.rem}</p>
            <p class="panel-note">still to be held</p>
          </div>
        </div>

        <div class="meter" data-band="${esc(me.band)}">
          <span class="meter-fill" style="width:${Math.min(100, Number(me.pct))}%"></span>
          <span class="meter-mark" style="left:${me.thresholdPct}%"></span>
        </div>
        <p class="meter-legend"><span>0%</span><span>threshold ${me.thresholdPct}%</span><span>100%</span></p>

        <div class="cost cost-lg" style="margin-top:26px;">${esc(me.cost)}</div>
        <p class="panel-note">We show the cost of missing a session, never an allowance to spend.</p>
      </div>`,
  }) + chapter({
    tint: true,
    eyebrow: "Looking ahead",
    title: "If you miss the next ones in turn.",
    body: `
      <div class="split-even">
        <div class="panel">
          <p class="panel-label">Cumulative</p>
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
      </div>`,
  }) + renderAskTeaser();
}

// ---------- dean / admin ----------

function renderOverviewBody(role) {
  if (state.overviewError) {
    return hero({ headline: "Cohort overview" }) + chapter({ body: errorBox(`Couldn't load the overview: ${state.overviewError}`) });
  }
  const o = state.overview;
  if (!o) {
    return hero({ headline: "Cohort overview" }) + chapter({ body: `<div class="notice">Loading the cohort…</div>` });
  }

  const heroBlock = hero({
    eyebrow: role === "dean" ? "School overview" : "Catalog admin",
    stat: num(o.atRisk),
    statAccent: o.atRisk > 0,
    headline: `of ${num(o.total)} enrolments ${o.atRisk === 1 ? "is" : "are"} flagged high risk.`,
    sub: o.namesRedacted
      ? `That is <strong>${o.atRiskPct.toFixed(1)}%</strong> of the cohort, across ${o.departments} departments. Student names arrive as <span class="mono">'REDACTED'</span> — the column mask is applied in Unity Catalog, before this page ever sees the data.`
      : `That is <strong>${o.atRiskPct.toFixed(1)}%</strong> of the cohort, across ${o.departments} departments and ${o.modules} modules. Names are visible to your role.`,
    aside: donut(
      BAND_ORDER.map((b) => ({ band: b, n: (o.riskBands.find((x) => x.band === b) || {}).n || 0 })),
      `${o.atRiskPct.toFixed(0)}<span class="pc">%</span>`,
      "high risk",
    ),
  });

  const tiles = [
    { k: "At-risk share", v: `${o.atRiskPct.toFixed(1)}%`, note: `${num(o.atRisk)} of ${num(o.total)}, weeks 1–6`, tone: toneFor(o.atRiskPct) },
    { k: "Enrolments visible", v: num(o.total), note: `${o.modules} module${o.modules === 1 ? "" : "s"} · ${o.departments} department${o.departments === 1 ? "" : "s"}`, tone: "info" },
    { k: "Names visible", v: o.namesRedacted ? "0" : num(o.total), note: o.namesRedacted ? "column mask applied" : "your role sees names", tone: o.namesRedacted ? "low" : "info" },
    { k: "Next session headcount", v: o.nextHeadcount != null ? num(o.nextHeadcount) : "—", note: o.nextSessionDate != null ? `forecast for day ${o.nextSessionDate}, aggregate only` : "aggregate only", tone: "medium" },
  ];

  return heroBlock + chapter({
    tight: true,
    body: `
      <div class="tiles">
        ${tiles.map((t) => `
          <div class="tile" data-band="${esc(t.tone)}">
            <p class="panel-label">${esc(t.k)}</p>
            <p class="tile-value band-text">${esc(t.v)}</p>
            <p class="panel-note">${t.note}</p>
          </div>`).join("")}
      </div>`,
  }) + chapter({
    tint: true,
    eyebrow: "The mix",
    title: "Every enrolment, by risk band.",
    note: "Risk is a model output over weeks 1–6. The attendance buffer beneath it is arithmetic. They are deliberately separate signals.",
    body: stackedBar(o.riskBands, o.total) + `
      <div class="split-even" style="margin-top:28px;">
        ${bandPanel("Risk bands", o.riskBands, o.total)}
        ${bandPanel("Attendance buffer bands", o.bufferBands, o.total)}
      </div>`,
  }) + chapter({
    eyebrow: "Where it concentrates",
    title: "Not evenly spread.",
    note: "Ranked by the number of high-risk students, not by share — that is where intervention capacity has to go.",
    body: `
      <div class="split-even">
        ${breakdownPanel("By department", o.byDepartment)}
        ${breakdownPanel("By module", o.byModule)}
      </div>`,
  }) + chapter({
    eyebrow: "Rows returned",
    title: o.namesRedacted ? "The mask, doing its job." : "Highest risk scores.",
    note: o.namesRedacted
      ? "This is the live result of the query, not a mock-up: <span class='mono'>mask_name</span> rewrote every name before the data left the catalog."
      : "Your role returns names intact.",
    body: `
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
      </p>`,
  }) + renderAskTeaser();
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
  return hero({
    eyebrow: "No role",
    stat: "0",
    headline: "rows — and that is the governance layer working correctly.",
    sub: "Unity Catalog doesn't recognise this account, so it returns nothing rather than guessing.",
  }) + chapter({
    eyebrow: "What happened",
    title: "Your account isn't mapped to a role yet.",
    body: `
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
  </section>`,
  });
}

// ---------- how it works ----------

function renderHow() {
  const g = GOVERNANCE;
  return hero({
    eyebrow: "How it works",
    stat: "1",
    headline: "set of tables. Four answers.",
    sub: "The same English question returns different data depending on who asks — enforced in Unity Catalog, beneath the agent, so it cannot be bypassed by querying the tables directly.",
  }) + chapter({
    eyebrow: "Lineage",
    title: "Raw to governed, in four steps.",
    body: `
      <div class="panel">
        <div class="lineage">
          ${g.lineage.map((l) => `
            <div class="lineage-row">
              <span class="lineage-layer">${esc(l.layer)}</span>
              <span><span class="lineage-what">${esc(l.what)}</span><span class="panel-note">${esc(l.note)}</span></span>
            </div>`).join("")}
        </div>
      </div>`,
  }) + chapter({
    eyebrow: "Effect",
    title: "What each role actually sees.",
    note: "Note the dean row: a dean is <em>not</em> department-scoped. It sees every row and differs from an admin only by the name mask.",
    body: `
      <div class="table">
        <div class="table-head"><span>Role</span><span>Rows returned</span><span>student_name</span></div>
        ${g.effect.map((e) => `
          <div class="table-row static">
            <span>${esc(e.role)}</span>
            <span>${esc(e.rows)}</span>
            <span class="mono">${esc(e.names)}</span>
          </div>`).join("")}
      </div>`,
  }) + chapter({
    eyebrow: "The policies themselves",
    title: "Quoted verbatim from the catalog.",
    note: "Not a diagram of what we intended — this is the DDL that is attached and enforced right now.",
    body: g.policies.map((p) => `
      <div class="panel" style="margin-bottom:18px;">
        <p class="panel-label">${esc(p.kind)} · <span class="mono">${esc(p.name)}</span></p>
        <p class="panel-note" style="margin-bottom:16px;">Attached to ${esc(p.on)}</p>
        <pre class="code">${esc(p.sql)}</pre>
      </div>`).join(""),
  }) + chapter({
    eyebrow: "Stated plainly",
    title: "What this is, and what it isn't.",
    body: `
      <div class="split-even">
        <div class="panel">
          <p class="panel-label">Disclosure</p>
          <p class="panel-body">Attendance is <strong>synthetic and disclosed</strong>, generated from a causal story over real engagement. The risk model trains on real OULAD outcomes — weeks 1–6 only, behavioural features only, no demographic column in any feature set.</p>
          <p class="panel-body">We quote no accuracy figure: a number from a nine-hour build on partly synthetic data would mislead.</p>
        </div>
        <div class="panel">
          <p class="panel-label">Deliberately not built</p>
          ${g.notBuilt.map((n) => `
            <div class="notbuilt">
              <span class="notbuilt-what">${esc(n.what)}</span>
              <span class="panel-note">${esc(n.why)}</span>
            </div>`).join("")}
        </div>
      </div>`,
  });
}

// ---------- render ----------

function renderBody() {
  // The landing page is deliberately static — it renders before identity resolves,
  // so the front door is never a spinner.
  if (state.page === "landing") return renderLanding();

  if (state.loading) return chapter({ body: `<div class="notice">Resolving your identity…</div>` });
  if (state.meError) return chapter({ body: errorBox(`Couldn't resolve who you are: ${state.meError}`) });
  if (state.page === "how") return renderHow();

  // In the demo nobody has an identity until they choose one. Live mode never reaches
  // this — there, role comes from role_map and cannot be picked.
  if (IS_DEMO && !state.me) return renderPicker();

  if (state.page === "chat") return renderChat();

  const role = state.me?.role;
  if (!role) return renderUnmapped();
  if (role === "advisor") return renderAdvisor();
  if (role === "student") return renderStudent();
  if (role === "dean" || role === "admin") return renderOverviewBody(role);
  return chapter({ body: errorBox(`Unrecognised role "${role}" in campus.ops.role_map.`) });
}

// Sections fade up as they enter view. Re-created on every render because innerHTML
// replaces the nodes the previous observer was watching.
let revealObserver = null;
function setupReveal() {
  if (revealObserver) revealObserver.disconnect();
  const els = root.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) {
    els.forEach((el) => el.classList.add("in"));
    return;
  }
  revealObserver = new IntersectionObserver((entries, obs) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add("in");
        obs.unobserve(entry.target);
      }
    }
  }, { rootMargin: "0px 0px -6% 0px", threshold: 0.04 });
  els.forEach((el) => revealObserver.observe(el));
}

function syncHeaderShadow() {
  const header = root.querySelector("header");
  if (header) header.classList.toggle("scrolled", window.scrollY > 4);
}

function render() {
  const inChat = state.page === "chat";
  document.body.classList.toggle("chat-mode", inChat);
  root.innerHTML = `${renderHeader()}<main>${renderBody()}</main>${inChat ? "" : renderFooter()}`;

  const input = document.getElementById("ask-input");
  if (input) {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submitAsk(input.value); input.value = ""; }
    });
    // Keep the caret where a chat user expects it, without stealing focus elsewhere.
    if (inChat && !state.asking) input.focus();
  }

  // Pin the thread to the newest message, the way a chat should behave.
  const thread = document.getElementById("chat-thread");
  if (thread) thread.scrollTop = thread.scrollHeight;

  setupReveal();
  syncHeaderShadow();
}

window.addEventListener("scroll", syncHeaderShadow, { passive: true });

// The agent preview is a div, so Enter/Space don't activate it for free.
root.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const el = e.target.closest('[role="button"][data-nav]');
  if (!el) return;
  e.preventDefault();
  setState({ page: el.dataset.nav });
});

async function submitAsk(question) {
  const q = (question ?? "").trim();
  if (!q || state.asking) return;

  state.page = "chat";
  state.messages.push({ from: "you", text: q });
  state.asking = true;
  render();

  // try/finally matters: without it a rejection would skip asking=false and leave the
  // composer disabled with no way to retry.
  try {
    const res = await askGenie(q, state.conversationId);
    state.conversationId = res.conversation_id || state.conversationId;
    state.messages.push({ from: "genie", text: res.answer });
  } catch (err) {
    state.messages.push({ from: "genie", text: `That didn't go through: ${err.message}`, error: true });
  } finally {
    state.asking = false;
    render();
  }
}

root.addEventListener("click", (e) => {
  if (e.target.closest("[data-theme-toggle]")) return toggleTheme();

  const pickRoleEl = e.target.closest("[data-pick-role]");
  if (pickRoleEl) return enterAsRole(pickRoleEl.dataset.pickRole);

  const navEl = e.target.closest("[data-nav]");
  if (navEl) {
    // "pick" isn't a page — it drops the current demo identity and returns to the chooser.
    if (navEl.dataset.nav === "pick") return leaveDemoRole();
    return setState({ page: navEl.dataset.nav });
  }

  const askEl = e.target.closest("[data-ask]");
  if (askEl) return submitAsk(askEl.dataset.ask);

  const selectEl = e.target.closest("[data-select]");
  if (selectEl) return setState({ sel: Number(selectEl.dataset.select) });

  const toggleEl = e.target.closest("[data-toggle='cohort']");
  if (toggleEl) return setState({ cohortExpanded: !state.cohortExpanded });

  const ctaEl = e.target.closest("[data-cta='ask']");
  if (ctaEl) {
    const el = document.getElementById("ask-input");
    const value = el ? el.value : "";
    if (el) el.value = "";
    return submitAsk(value);
  }

  const actionEl = e.target.closest("[data-action]");
  if (actionEl) {
    const key = `${actionEl.dataset.actionId}:${actionEl.dataset.action}`;
    return setState({ loggedActions: { ...state.loggedActions, [key]: true } });
  }
});

boot();
