/* ===================================================================
   Revenant — dashboard logic
   No framework, no build step, no CDN. Every number on screen comes
   from a live call to the FastAPI backend.
   =================================================================== */

const API = "";
const $ = (id) => document.getElementById(id);

const PALETTE = {
  terracotta: "#e07a5f", navy: "#3d405b", sage: "#81b29a",
  sand: "#f2cc8f", navySoft: "#8a8da6", clay: "#c25e45",
};
const REASON_COLOR = {
  network_drop: "#e07a5f", wrong_otp: "#c25e45", bank_timeout: "#3d405b",
  insufficient_funds: "#81b29a", gateway_error: "#8a8da6", card_declined: "#c9a227",
};

const rupee = (n) =>
  "\u20B9" + Math.round(n).toLocaleString("en-IN");
const rupeeShort = (n) =>
  n >= 1e7 ? "\u20B9" + (n / 1e7).toFixed(2) + " Cr"
  : n >= 1e5 ? "\u20B9" + (n / 1e5).toFixed(2) + " L"
  : rupee(n);
const pct = (x, d = 1) => (100 * x).toFixed(d) + "%";
const titleize = (s) => String(s).replace(/_/g, " ");

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  if (!res.ok) throw new Error(path + " -> " + res.status);
  return res.json();
}

/* ============================ animation ============================ */
function countUp(el, target, format, ms = 1400) {
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / ms);
    const eased = 1 - Math.pow(1 - k, 3);
    el.textContent = format(target * eased);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ============================ SVG helpers ========================== */
const svgEl = (w, h) =>
  `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="img">`;

function barChart(rows, { width = 900, rowH = 54, pad = 150 } = {}) {
  const h = rows.length * rowH + 34;
  const max = Math.max(...rows.map((r) => r.value), 1);
  const scale = (v) => (v / max) * (width - pad - 130);
  let s = svgEl(width, h);
  rows.forEach((r, i) => {
    const y = i * rowH + 12;
    const w = Math.max(scale(r.value), 2);
    s += `<text class="bar-label" x="0" y="${y + 20}">${r.label}</text>`;
    s += `<rect x="${pad}" y="${y + 7}" width="${width - pad - 130}" height="21" rx="6"
           fill="rgba(61,64,91,.05)"/>`;
    s += `<rect x="${pad}" y="${y + 7}" width="0" height="21" rx="6" fill="${r.color}">
            <animate attributeName="width" from="0" to="${w}" dur="1s"
                     begin="${i * 0.14}s" fill="freeze"
                     calcMode="spline" keySplines="0.22 0.8 0.3 1" keyTimes="0;1"/>
          </rect>`;
    if (r.ci) {
      const lo = pad + scale(r.ci[0]), hi = pad + scale(r.ci[1]);
      s += `<line x1="${lo}" x2="${hi}" y1="${y + 17.5}" y2="${y + 17.5}"
             stroke="rgba(255,253,246,.9)" stroke-width="1.5"/>
            <line x1="${lo}" x2="${lo}" y1="${y + 12}" y2="${y + 23}" stroke="rgba(255,253,246,.9)" stroke-width="1.5"/>
            <line x1="${hi}" x2="${hi}" y1="${y + 12}" y2="${y + 23}" stroke="rgba(255,253,246,.9)" stroke-width="1.5"/>`;
    }
    s += `<text class="bar-value" x="${pad + (width - pad - 130) + 10}" y="${y + 22}">${r.display}</text>`;
    if (r.note)
      s += `<text class="axis-text" x="0" y="${y + 36}">${r.note}</text>`;
  });
  return s + "</svg>";
}

function lineChart(series, { width = 900, height = 320, xTicks, yLabel } = {}) {
  const m = { l: 46, r: 18, t: 14, b: 34 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const xs = series[0].points.map((p) => p.x);
  const xScale = (i) => m.l + (i / (xs.length - 1)) * iw;
  const yScale = (v) => m.t + ih - v * ih;

  let s = svgEl(width, height);
  for (let g = 0; g <= 4; g++) {
    const y = m.t + (g / 4) * ih;
    s += `<line class="grid-line" x1="${m.l}" x2="${width - m.r}" y1="${y}" y2="${y}"/>`;
    s += `<text class="axis-text" x="${m.l - 9}" y="${y + 3.5}" text-anchor="end">${
      ((1 - g / 4) * 100).toFixed(0)}%</text>`;
  }
  (xTicks || xs).forEach((lab, i) => {
    if (i % 2 !== 0 && xs.length > 8) return;
    s += `<text class="axis-text" x="${xScale(i)}" y="${height - 12}" text-anchor="middle">${lab}</text>`;
  });
  if (yLabel)
    s += `<text class="axis-text" x="${m.l - 34}" y="${m.t + ih / 2}"
           transform="rotate(-90 ${m.l - 34} ${m.t + ih / 2})" text-anchor="middle">${yLabel}</text>`;

  series.forEach((ser, k) => {
    const d = ser.points.map((p, i) => `${i ? "L" : "M"}${xScale(i).toFixed(1)},${yScale(p.y).toFixed(1)}`).join(" ");
    s += `<path d="${d}" fill="none" stroke="${ser.color}" stroke-width="2.4"
            stroke-linejoin="round" stroke-linecap="round"
            stroke-dasharray="3000" stroke-dashoffset="3000">
            <animate attributeName="stroke-dashoffset" from="3000" to="0"
                     dur="1.5s" begin="${k * 0.12}s" fill="freeze"/>
          </path>`;
    const peak = ser.points.reduce((a, b) => (b.y > a.y ? b : a));
    const pi = ser.points.indexOf(peak);
    s += `<circle cx="${xScale(pi)}" cy="${yScale(peak.y)}" r="4"
            fill="${ser.color}" stroke="#fffdf6" stroke-width="1.8" opacity="0">
            <animate attributeName="opacity" from="0" to="1" dur=".4s"
                     begin="${1.4 + k * 0.12}s" fill="freeze"/></circle>`;
  });
  return s + "</svg>";
}

function scatterChart(points, { width = 420, height = 300 } = {}) {
  const m = { l: 40, r: 14, t: 12, b: 32 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const hi = Math.max(...points.flatMap((p) => [p.x, p.y]), 0.1) * 1.08;
  const X = (v) => m.l + (v / hi) * iw;
  const Y = (v) => m.t + ih - (v / hi) * ih;
  let s = svgEl(width, height);
  s += `<line class="grid-line" x1="${X(0)}" y1="${Y(0)}" x2="${X(hi)}" y2="${Y(hi)}"
         stroke-dasharray="4 4" stroke="rgba(61,64,91,.28)"/>`;
  s += `<text class="axis-text" x="${X(hi) - 4}" y="${Y(hi) - 8}" text-anchor="end">perfect calibration</text>`;
  s += `<line class="grid-line" x1="${m.l}" x2="${m.l}" y1="${m.t}" y2="${m.t + ih}"/>`;
  s += `<line class="grid-line" x1="${m.l}" x2="${width - m.r}" y1="${m.t + ih}" y2="${m.t + ih}"/>`;
  const d = points.map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ");
  s += `<path d="${d}" fill="none" stroke="${PALETTE.terracotta}" stroke-width="2"/>`;
  points.forEach((p) => {
    s += `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="3.6" fill="${PALETTE.terracotta}"
           stroke="#fffdf6" stroke-width="1.4"/>`;
  });
  s += `<text class="axis-text" x="${m.l + iw / 2}" y="${height - 6}" text-anchor="middle">predicted probability</text>`;
  return s + "</svg>";
}

/* ============================== ledger ============================= */
async function loadLedger() {
  const d = await api("/api/ledger");
  const rev = d.policies.revenant;
  const leg = d.policies.legacy_retry_T30;

  $("hero-batch").textContent = d.batch_size.toLocaleString("en-IN");
  countUp($("s-recovered"), rev.recovered, rupeeShort);
  $("s-ci").textContent =
    `95% CI ${rupeeShort(rev.ci95[0])} – ${rupeeShort(rev.ci95[1])} (2,000 bootstrap resamples)`;

  countUp($("s-uplift"), d.uplift_pct, (v) => "+" + pct(v));
  $("s-uplift-abs").textContent = `+${rupeeShort(d.uplift_vs_legacy)} over a fixed T+30 min retry`;

  countUp($("s-attempts"), d.attempt_reduction_pct, (v) => "\u2212" + pct(v));
  $("s-attempts-sub").textContent =
    `${rev.actions.toLocaleString("en-IN")} vs ${leg.actions.toLocaleString("en-IN")} attempts — more money, less contact`;

  countUp($("s-share"), rev.share_of_exposure, (v) => pct(v));
  $("s-share-sub").textContent =
    `of ${rupeeShort(d.exposure)} at risk (legacy retry: ${pct(leg.share_of_exposure)})`;

  $("ledger-chart").innerHTML = barChart([
    { label: "Do nothing", value: d.policies.do_nothing.recovered, color: PALETTE.navySoft,
      display: rupeeShort(d.policies.do_nothing.recovered), note: "the honest floor" },
    { label: "Fixed retry T+30", value: leg.recovered, color: PALETTE.navy,
      display: rupeeShort(leg.recovered), ci: leg.ci95,
      note: `${leg.actions.toLocaleString("en-IN")} attempts · industry default` },
    { label: "Revenant", value: rev.recovered, color: PALETTE.terracotta,
      display: rupeeShort(rev.recovered), ci: rev.ci95,
      note: `${rev.actions.toLocaleString("en-IN")} attempts · guard-enforced` },
  ]);

  const row = (name, p, hl) => `
    <tr class="${hl ? "highlight" : ""}">
      <td>${name}</td>
      <td>${rupee(p.recovered)}</td>
      <td>${pct(p.recovery_rate, 2)}</td>
      <td>${p.actions.toLocaleString("en-IN")}</td>
      <td>${p.nudges.toLocaleString("en-IN")}</td>
      <td>${p.blocked.toLocaleString("en-IN")}</td>
      <td>${p.escalated.toLocaleString("en-IN")}</td>
    </tr>`;
  $("ledger-table").innerHTML = `
    <table>
      <thead><tr>
        <th>Policy</th><th>Recovered</th><th>Recovery rate</th><th>Attempts</th>
        <th>Nudges</th><th>Blocked by guard</th><th>Escalated</th>
      </tr></thead>
      <tbody>
        ${row("Do nothing", d.policies.do_nothing)}
        ${row("Fixed retry T+30, cap 3", leg)}
        ${row("Revenant", rev, true)}
      </tbody>
    </table>`;
}

/* ============================== curves ============================= */
function fmtDelay(m) {
  if (m === 0) return "now";
  if (m < 60) return m + "m";
  if (m < 1440) return (m / 60) + "h";
  return (m / 1440) + "d";
}

async function loadCurves() {
  const d = await api("/api/curve-by-reason?amount=2500&hour=20");
  const series = d.series.map((s) => ({
    name: s.reason,
    color: REASON_COLOR[s.reason] || PALETTE.navy,
    points: s.points.map((p) => ({ x: p.delay_min, y: p.p_success })),
  }));
  const ticks = d.series[0].points.map((p) => fmtDelay(p.delay_min));
  $("curve-chart").innerHTML = lineChart(series, { xTicks: ticks, yLabel: "P(success)" });
  $("curve-legend").innerHTML = series.map((s) =>
    `<span class="legend-item"><span class="legend-swatch" style="background:${s.color}"></span>${titleize(s.name)}</span>`
  ).join("");

  const best = (name) => {
    const s = series.find((x) => x.name === name);
    if (!s) return null;
    return s.points.reduce((a, b) => (b.y > a.y ? b : a));
  };
  const drop = best("network_drop"), funds = best("insufficient_funds");
  if (drop && funds) {
    $("curve-callout").innerHTML =
      `A dropped session peaks at <strong>${fmtDelay(drop.x)}</strong> and is worth almost nothing an hour later. ` +
      `An empty account peaks at <strong>${fmtDelay(funds.x)}</strong> and is worth almost nothing immediately. ` +
      `Revenant learns this from logged data — the retry delay is the model's single highest-gain feature.`;
  }
}

/* ============================== console ============================ */
const state = { records: [], selected: null,
                counts: { total: 0, actions: 0, nudges: 0, blocked: 0,
                          escalated: 0, agent: 0, ev: 0 } };

function renderCounters() {
  const c = state.counts;
  const items = [
    ["Decisions", c.total], ["Actions taken", c.actions], ["Nudges sent", c.nudges],
    ["Blocked", c.blocked], ["Escalated", c.escalated], ["Agent-routed", c.agent],
    ["Expected recovery", rupeeShort(c.ev)],
  ];
  $("counters").innerHTML = items.map(([k, v]) =>
    `<div class="counter"><div class="k">${k}</div><div class="v">${
      typeof v === "number" ? v.toLocaleString("en-IN") : v}</div></div>`).join("");
}

function actionLine(r) {
  const a = r.final_action;
  if (a.strategy === "no_action") return "No action";
  if (a.strategy === "escalate_human") return "Escalate to human review";
  const when = a.delay_min <= 5 ? "now" : "+" + fmtDelay(a.delay_min);
  const via = a.channel === "none" ? "" : ` via ${a.channel}`;
  return `${titleize(a.strategy)} ${when}${via}`;
}

function renderCard(r) {
  const el = document.createElement("div");
  el.className = `card v-${r.guard_verdict}`;
  el.dataset.txn = r.txn_id;
  const srcLabel = r.decision_source === "llm_agent" ? "LLM AGENT" : "OPTIMISER";
  el.innerHTML = `
    <div class="card-top">
      <span class="card-amt">${rupee(r.amount)}</span>
      <span class="card-reason">${r.reason.toUpperCase()}</span>
    </div>
    <div class="card-action">${actionLine(r)}</div>
    <div class="card-meta">
      <span class="badge b-${r.guard_verdict}">${r.guard_verdict}</span>
      <span class="badge b-src">${srcLabel}</span>
      <span class="badge b-src">${r.latency_ms} ms</span>
    </div>`;
  el.addEventListener("click", () => select(r.txn_id));
  return el;
}

function select(txnId) {
  state.selected = txnId;
  document.querySelectorAll(".card").forEach((c) =>
    c.classList.toggle("selected", c.dataset.txn === txnId));
  const r = state.records.find((x) => x.txn_id === txnId);
  if (r) renderDetail(r);
}

function renderDetail(r) {
  const maxEv = Math.max(...r.top_candidates.map((c) => c.expected_value), 1);
  const cands = r.top_candidates.map((c, i) => `
    <div class="cand ${i === 0 ? "top" : ""}">
      <div class="cand-row">
        <span><strong>${titleize(c.strategy)}</strong> ${
          c.delay_min <= 5 ? "now" : "+" + fmtDelay(c.delay_min)}${
          c.channel === "none" ? "" : " · " + c.channel}</span>
        <span>${pct(c.p_success, 0)} · ${rupee(c.expected_value)}</span>
      </div>
      <div class="cand-bar"><div class="cand-fill" style="width:${
        Math.max(2, (c.expected_value / maxEv) * 100)}%"></div></div>
    </div>`).join("");

  const notes = r.guard_notes.length
    ? r.guard_notes.map((n) => `<div class="step-v">${n}</div>`).join("")
    : `<div class="step-v">No rule triggered — action executed as proposed.</div>`;

  $("detail").innerHTML = `
    <h3>${rupee(r.amount)} · ${titleize(r.reason)}</h3>
    <div class="sub">${r.txn_id} · ${r.method} · ${r.bank} · ${String(r.hour).padStart(2, "0")}:00
      · attempt ${r.prior_attempts + 1}</div>

    <div class="rationale">${r.rationale || ""}</div>

    <ul class="chain">
      <li>
        <div class="step-k">1 — Candidate sweep</div>
        <div class="step-v">${r.candidates_evaluated} actions scored by
          <code>${r.model_version}</code>. Top five:</div>
        <div style="margin-top:10px">${cands}</div>
      </li>
      <li>
        <div class="step-k">2 — Decision source</div>
        <div class="step-v">${
          r.decision_source === "llm_agent"
            ? "Top two actions were within 8% expected value — routed to the LLM agent to break the tie on customer-experience grounds."
            : "Clear expected-value winner — decided by the optimiser without an LLM call."}</div>
      </li>
      <li class="step-guard">
        <div class="step-k">3 — Compliance guard · ${r.guard_verdict}</div>
        ${notes}
      </li>
      <li>
        <div class="step-k">4 — Executed</div>
        <div class="step-v">${actionLine(r)}</div>
      </li>
    </ul>

    <dl class="kv">
      <dt>timestamp</dt><dd>${r.ts}</dd>
      <dt>feature hash</dt><dd>${r.feature_hash}</dd>
      <dt>proposed</dt><dd>${r.proposed_action.strategy} / ${r.proposed_action.delay_min}m / ${r.proposed_action.channel}</dd>
      <dt>final</dt><dd>${r.final_action.strategy} / ${r.final_action.delay_min}m / ${r.final_action.channel}</dd>
      <dt>guard code</dt><dd>${r.guard_code}</dd>
      <dt>latency</dt><dd>${r.latency_ms} ms</dd>
    </dl>`;
}

function tally(r) {
  const c = state.counts;
  c.total += 1;
  if (r.guard_verdict === "BLOCK") c.blocked += 1;
  else if (r.guard_verdict === "ESCALATE") c.escalated += 1;
  else if (r.final_action.strategy !== "no_action") {
    c.actions += 1;
    if (r.final_action.channel !== "none") c.nudges += 1;
    c.ev += Math.max(0, r.best_ev || 0);
  }
  if (r.decision_source === "llm_agent") c.agent += 1;
}

async function runBatch(n = 22) {
  const btn = $("btn-run");
  btn.disabled = true;
  btn.textContent = "Streaming…";
  try {
    const { transactions } = await api(`/api/sample?n=${n}`);
    const feed = $("feed");
    if (feed.querySelector(".empty")) feed.innerHTML = "";
    for (const t of transactions) {
      const rec = await api("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(t),
      });
      state.records.unshift(rec);
      tally(rec);
      feed.prepend(renderCard(rec));
      renderCounters();
      if (!state.selected) select(rec.txn_id);
      await new Promise((r) => setTimeout(r, 190));
    }
  } catch (e) {
    $("feed").innerHTML = `<div class="empty">Backend unreachable — ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Run recovery batch";
  }
}

/* ============================ rulebook / model ===================== */
async function loadRulebook() {
  const d = await api("/api/rulebook");
  $("rulebook").innerHTML = d.rules.map((r) =>
    `<div class="rule"><div class="rule-code">${r.code}</div>
     <div class="rule-text">${r.text}</div></div>`).join("");
}

async function loadModelCard() {
  const m = await api("/api/model-card");
  const metric = (k, v, n) =>
    `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div><div class="n">${n}</div></div>`;
  $("model-metrics").innerHTML = [
    metric("ROC AUC", m.auc.toFixed(3), `${m.n_holdout.toLocaleString("en-IN")} held-out rows`),
    metric("Brier score", m.brier.toFixed(3), "lower is better"),
    metric("Log loss", m.log_loss.toFixed(3), "held-out"),
    metric("Calibration", `${m.mean_predicted.toFixed(3)} / ${m.base_rate.toFixed(3)}`,
           "mean predicted vs. observed base rate"),
  ].join("");

  $("calib-chart").innerHTML = scatterChart(
    m.calibration.map((c) => ({ x: c.predicted, y: c.observed })));

  const top = m.feature_importance.slice(0, 7);
  const max = top[0].gain;
  $("gain-chart").innerHTML = barChart(
    top.map((f, i) => ({
      label: titleize(f.feature),
      value: f.gain,
      color: i === 0 ? PALETTE.terracotta : PALETTE.sage,
      display: (f.gain / 1000).toFixed(0) + "k",
    })), { width: 430, rowH: 34, pad: 120 });
}

/* ============================== boot =============================== */
async function boot() {
  const h = $("health");
  try {
    const s = await api("/api/health");
    h.className = "pill pill-ok";
    h.textContent = `${s.model} · agent ${s.agent_configured ? "live" : "offline"}`;
  } catch {
    h.className = "pill pill-bad";
    h.textContent = "backend offline";
  }
  const jobs = [
    [loadLedger, "ledger-chart", "Run: python -m scripts.evaluate"],
    [loadCurves, "curve-chart", "Model artifact missing — run: python -m scripts.train_model"],
    [loadRulebook, "rulebook", "Backend offline"],
    [loadModelCard, "model-metrics", "Run: python -m scripts.train_model"],
  ];
  for (const [fn, target, msg] of jobs) {
    try { await fn(); }
    catch { $(target).innerHTML = `<div class="empty">${msg}</div>`; }
  }
  renderCounters();
  $("btn-run").addEventListener("click", () => runBatch());
  $("btn-clear").addEventListener("click", () => {
    state.records = []; state.selected = null;
    state.counts = { total: 0, actions: 0, nudges: 0, blocked: 0, escalated: 0, agent: 0, ev: 0 };
    $("feed").innerHTML = `<div class="empty">Run a batch to stream live decisions.</div>`;
    $("detail").innerHTML = `<div class="empty">Select a decision to inspect the full reasoning chain.</div>`;
    renderCounters();
  });
}

document.addEventListener("DOMContentLoaded", boot);

/* ===================================================================
   Copilot + Sandbox
   =================================================================== */
const SUGGESTIONS = [
  "how much did we recover vs legacy?",
  "why are we blocking payments?",
  "summarise the last decisions",
  "what if we raised the retry cap to 5?",
];

function initCopilot() {
  const chips = $("copilot-chips");
  if (chips) {
    chips.innerHTML = SUGGESTIONS
      .map((s) => `<button class="chip" type="button">${s}</button>`)
      .join("");
    chips.querySelectorAll(".chip").forEach((el) => {
      el.addEventListener("click", () => askCopilot(el.textContent));
    });
  }
  const send = $("copilot-send");
  const input = $("copilot-input");
  if (send && input) {
    send.addEventListener("click", () => {
      const q = input.value.trim();
      if (q) { askCopilot(q); input.value = ""; }
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") send.click();
    });
  }
}

async function askCopilot(question) {
  const log = $("copilot-log");
  if (!log) return;
  if (log.querySelector(".empty")) log.innerHTML = "";

  const turn = document.createElement("div");
  turn.className = "turn";
  turn.innerHTML = `<div class="turn-q">${question}</div><div class="thinking"><i></i><i></i><i></i></div>`;
  log.appendChild(turn);
  log.scrollTop = log.scrollHeight;

  try {
    const r = await api("/api/copilot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const toolStrip = r.tool_calls
      .map((c, i) => `<span class="tool-tag" data-i="${i}">${c.tool}</span>`)
      .join("");
    turn.innerHTML = `
      <div class="turn-q">${question}</div>
      <div class="turn-a">${r.answer}</div>
      <div class="tool-strip">${toolStrip}</div>`;
    turn.querySelectorAll(".tool-tag").forEach((tag) => {
      tag.addEventListener("click", () => {
        const i = Number(tag.dataset.i);
        let body = turn.querySelector(".tool-body");
        if (body) { body.remove(); return; }
        body = document.createElement("div");
        body.className = "tool-body";
        body.textContent = JSON.stringify(r.tool_calls[i].result, null, 2);
        turn.appendChild(body);
      });
    });
  } catch (e) {
    turn.innerHTML = `<div class="turn-q">${question}</div><div class="turn-a">Couldn't reach the Copilot backend — is the server running?</div>`;
  }
  log.scrollTop = log.scrollHeight;
}

const SLIDER_DEFS = [
  { key: "MAX_ATTEMPTS", label: "Retry attempt cap", note: "Total re-attempts permitted per payment." },
  { key: "MIN_SPACING_MIN", label: "Minimum spacing (min)", note: "Minimum gap between consecutive attempts." },
  { key: "DND_START", label: "Quiet hours start (IST)", note: "No customer nudges from this hour." },
  { key: "DND_END", label: "Quiet hours end (IST)", note: "Nudges resume at this hour." },
  { key: "HUMAN_REVIEW_AMOUNT", label: "Human-review ceiling (Rs)", note: "Payments at or above this amount escalate to a human." },
];

let whatifDefaults = null;
let sliderState = {};

async function initSandbox() {
  const wrap = $("sliders");
  if (!wrap) return;
  try {
    const d = await api("/api/whatif/defaults");
    whatifDefaults = d.defaults;
    sliderState = { ...d.defaults };
    wrap.innerHTML = SLIDER_DEFS.map((s) => {
      const [lo, hi] = d.tunable[s.key];
      const step = s.key === "HUMAN_REVIEW_AMOUNT" ? 500 : 1;
      return `
        <div class="slider-row">
          <div class="slider-head">
            <span class="slider-name">${s.label}</span>
            <span class="slider-val" id="val-${s.key}">${d.defaults[s.key]}</span>
          </div>
          <input type="range" id="sl-${s.key}" min="${lo}" max="${hi}" step="${step}"
                 value="${d.defaults[s.key]}" />
          <div class="slider-note">${s.note}</div>
        </div>`;
    }).join("") + `<div class="locked">RISK_BLOCK_NO_RETRY — always enforced, not tunable</div>`;

    SLIDER_DEFS.forEach((s) => {
      const el = $(`sl-${s.key}`);
      el.addEventListener("input", () => {
        sliderState[s.key] = Number(el.value);
        $(`val-${s.key}`).textContent = el.value;
      });
    });
  } catch {
    wrap.innerHTML = `<div class="empty">Backend offline — can't load sandbox controls.</div>`;
  }

  $("btn-whatif")?.addEventListener("click", runWhatif);
  $("btn-whatif-reset")?.addEventListener("click", () => {
    if (!whatifDefaults) return;
    SLIDER_DEFS.forEach((s) => {
      sliderState[s.key] = whatifDefaults[s.key];
      const el = $(`sl-${s.key}`);
      if (el) { el.value = whatifDefaults[s.key]; $(`val-${s.key}`).textContent = whatifDefaults[s.key]; }
    });
    $("whatif-result").innerHTML = `<div class="empty">Move a control, then run the scenario.</div>`;
  });
}

async function runWhatif() {
  const out = $("whatif-result");
  if (!whatifDefaults) return;
  const overrides = {};
  Object.keys(sliderState).forEach((k) => {
    if (sliderState[k] !== whatifDefaults[k]) overrides[k] = sliderState[k];
  });
  if (Object.keys(overrides).length === 0) {
    out.innerHTML = `<div class="empty">Nothing changed from the shipped config yet.</div>`;
    return;
  }
  out.innerHTML = `<div class="empty">Running scenario on the sandbox batch…</div>`;
  try {
    const r = await api("/api/whatif", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(overrides),
    });
    const up = r.delta_revenue >= 0;
    out.innerHTML = `
      <div class="wi-head ${up ? "up" : "down"}">${up ? "+" : "-"}${rupee(Math.abs(r.delta_revenue))}</div>
      <div class="wi-sub">vs. the shipped configuration, on a ${r.sample_size.toLocaleString("en-IN")}-payment sample</div>
      <div class="wi-grid">
        <div class="wi-cell"><div class="k">Attempts</div><div class="v">${r.delta_actions >= 0 ? "+" : ""}${r.delta_actions}</div></div>
        <div class="wi-cell"><div class="k">Nudges</div><div class="v">${r.delta_nudges >= 0 ? "+" : ""}${r.delta_nudges}</div></div>
        <div class="wi-cell"><div class="k">Blocked Δ</div><div class="v">${r.delta_blocked >= 0 ? "+" : ""}${r.delta_blocked}</div></div>
        <div class="wi-cell"><div class="k">Escalated Δ</div><div class="v">${r.delta_escalated >= 0 ? "+" : ""}${r.delta_escalated}</div></div>
      </div>
      <div class="wi-note">Risk/AML blocks are never tunable — RISK_BLOCK_NO_RETRY holds in every scenario, including this one.</div>`;
  } catch {
    out.innerHTML = `<div class="empty">Couldn't reach the sandbox backend.</div>`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initCopilot();
  initSandbox();
});
