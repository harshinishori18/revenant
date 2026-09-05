/* ===================================================================
   Revenant — dashboard logic
   No framework, no build step, no CDN. Every figure on screen comes
   from a live call to the FastAPI backend.
   =================================================================== */

const $ = (id) => document.getElementById(id);

const C = {
  terracotta: "#bc6c25", clay: "#9c5619", navy: "#606c38",
  ocean: "#606c38", oceanD: "#7d8c4c", plum: "#dda15e", navySoft: "#a8ac8e",
};
const REASON_COLOR = {
  network_drop: "#bc6c25",        // caramel — fastest decay, the hot line
  wrong_otp: "#dda15e",           // tan
  bank_timeout: "#283618",        // bark — the step function
  insufficient_funds: "#606c38",  // olive — the slow riser
  gateway_error: "#8a927a",       // sage grey
  card_declined: "#9c5619",       // deep caramel
};
const REASON_PLAIN = {
  network_drop: "Internet dropped mid-payment",
  wrong_otp: "Wrong OTP entered",
  bank_timeout: "Bank did not respond",
  insufficient_funds: "Not enough money in account",
  gateway_error: "Payment gateway error",
  card_declined: "Card declined",
  risk_block: "Blocked for fraud/risk",
};
const STRATEGY_PLAIN = {
  no_action: "Leave it alone",
  instant_retry: "Try again right away",
  delayed_retry: "Try again later",
  nudge_then_retry: "Message the customer, then try again",
  suggest_alt_method: "Suggest a different payment method",
  escalate_human: "Send to a person to review",
};

const rupee = (n) => "\u20B9" + Math.round(n).toLocaleString("en-IN");
const rupeeShort = (n) => {
  const a = Math.abs(n);
  if (a >= 1e7) return "\u20B9" + (n / 1e7).toFixed(2) + " Cr";
  if (a >= 1e5) return "\u20B9" + (n / 1e5).toFixed(2) + " L";
  return rupee(n);
};
const pct = (x, d = 1) => (100 * x).toFixed(d) + "%";
const nfmt = (n) => Number(n).toLocaleString("en-IN");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function fmtDelay(m) {
  if (m <= 0) return "now";
  if (m < 60) return Math.round(m) + " min";
  if (m < 1440) return (m / 60).toFixed(m % 60 ? 1 : 0) + " h";
  return (m / 1440).toFixed(m % 1440 ? 1 : 0) + " d";
}

async function api(path, opts = {}, ms = 120000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    const res = await fetch(path, { ...opts, signal: ctrl.signal });
    if (!res.ok) {
      let msg = res.status + "";
      try { msg = (await res.json()).detail || msg; } catch { /* ignore */ }
      throw new Error(msg);
    }
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

/* ============================ chrome ============================== */
function initCursor() {
  // The native cursor stays visible. A soft warm halo trails behind it, which
  // reads as ambient lighting rather than a replacement pointer.
  const glow = $("cursor-glow");
  if (!glow || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  let gx = innerWidth / 2, gy = innerHeight / 2, tx = gx, ty = gy;
  addEventListener("mousemove", (e) => {
    tx = e.clientX; ty = e.clientY;
    glow.classList.add("on");
  }, { passive: true });
  addEventListener("mouseleave", () => glow.classList.remove("on"));
  (function loop() {
    gx += (tx - gx) * 0.09; gy += (ty - gy) * 0.09;
    glow.style.transform = `translate(${gx.toFixed(1)}px, ${gy.toFixed(1)}px)`;
    requestAnimationFrame(loop);
  })();
}

function initReveal() {
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
  }, { threshold: 0.06, rootMargin: "0px 0px -40px 0px" });
  document.querySelectorAll(".reveal").forEach((el) => io.observe(el));
}

function countUp(el, target, format, ms = 1300) {
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / ms);
    el.textContent = format(target * (1 - Math.pow(1 - k, 3)));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ============================ SVG charts =========================== */
const open = (w, h) => `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="img">`;

function barChart(rows, { width = 900, rowH = 54, pad = 158, valW = 128 } = {}) {
  const h = rows.length * rowH + 30;
  const max = Math.max(...rows.map((r) => r.value), 1);
  const track = width - pad - valW;
  const sc = (v) => (v / max) * track;
  let s = open(width, h);
  rows.forEach((r, i) => {
    const y = i * rowH + 12;
    s += `<text class="bar-label" x="0" y="${y + 20}">${esc(r.label)}</text>`;
    s += `<rect x="${pad}" y="${y + 7}" width="${track}" height="21" rx="6" fill="rgba(40,54,24,.06)"/>`;
    s += `<rect x="${pad}" y="${y + 7}" width="0" height="21" rx="6" fill="${r.color}">
            <animate attributeName="width" from="0" to="${Math.max(sc(r.value), 2)}" dur="1s"
              begin="${i * 0.14}s" fill="freeze" calcMode="spline"
              keySplines="0.22 0.8 0.3 1" keyTimes="0;1"/></rect>`;
    if (r.ci) {
      const lo = pad + sc(r.ci[0]), hi = pad + sc(r.ci[1]);
      s += `<line x1="${lo}" x2="${hi}" y1="${y + 17.5}" y2="${y + 17.5}" stroke="rgba(255,253,244,.92)" stroke-width="1.5"/>
            <line x1="${lo}" x2="${lo}" y1="${y + 12}" y2="${y + 23}" stroke="rgba(255,253,244,.92)" stroke-width="1.5"/>
            <line x1="${hi}" x2="${hi}" y1="${y + 12}" y2="${y + 23}" stroke="rgba(255,253,244,.92)" stroke-width="1.5"/>`;
    }
    s += `<text class="bar-value" x="${pad + track + 10}" y="${y + 22}">${esc(r.display)}</text>`;
    if (r.note) s += `<text class="axis-text" x="0" y="${y + 36}">${esc(r.note)}</text>`;
  });
  return s + "</svg>";
}

function lineChart(series, { width = 900, height = 320, xTicks = [], yLabel } = {}) {
  const m = { l: 48, r: 18, t: 14, b: 34 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const n = series[0].points.length;
  const X = (i) => m.l + (i / (n - 1)) * iw;
  const Y = (v) => m.t + ih - v * ih;
  let s = open(width, height);
  for (let g = 0; g <= 4; g++) {
    const y = m.t + (g / 4) * ih;
    s += `<line class="grid-line" x1="${m.l}" x2="${width - m.r}" y1="${y}" y2="${y}"/>
          <text class="axis-text" x="${m.l - 9}" y="${y + 3.5}" text-anchor="end">${((1 - g / 4) * 100).toFixed(0)}%</text>`;
  }
  xTicks.forEach((lab, i) => {
    if (i % 2 !== 0 && n > 8) return;
    s += `<text class="axis-text" x="${X(i)}" y="${height - 12}" text-anchor="middle">${esc(lab)}</text>`;
  });
  if (yLabel) s += `<text class="axis-text" x="${m.l - 35}" y="${m.t + ih / 2}"
      transform="rotate(-90 ${m.l - 35} ${m.t + ih / 2})" text-anchor="middle">${esc(yLabel)}</text>`;
  series.forEach((ser, k) => {
    const d = ser.points.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ");
    s += `<path d="${d}" fill="none" stroke="${ser.color}" stroke-width="2.4" stroke-linejoin="round"
            stroke-linecap="round" stroke-dasharray="3000" stroke-dashoffset="3000">
            <animate attributeName="stroke-dashoffset" from="3000" to="0" dur="1.5s"
              begin="${k * 0.12}s" fill="freeze"/></path>`;
    const peak = ser.points.reduce((a, b) => (b.y > a.y ? b : a));
    const pi = ser.points.indexOf(peak);
    s += `<circle cx="${X(pi)}" cy="${Y(peak.y)}" r="4.2" fill="${ser.color}" stroke="#fffdf4"
            stroke-width="1.8" opacity="0"><animate attributeName="opacity" from="0" to="1"
            dur=".4s" begin="${1.4 + k * 0.12}s" fill="freeze"/></circle>`;
  });
  return s + "</svg>";
}

function areaChart(values, { width = 900, height = 130, color = C.ocean } = {}) {
  const m = { l: 6, r: 6, t: 12, b: 16 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const max = Math.max(...values, 1);
  const n = Math.max(values.length, 2);
  const X = (i) => m.l + (i / (n - 1)) * iw;
  const Y = (v) => m.t + ih - (v / max) * ih;
  const pts = values.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`);
  const line = "M" + pts.join(" L");
  const area = `${line} L${X(values.length - 1).toFixed(1)},${m.t + ih} L${X(0).toFixed(1)},${m.t + ih} Z`;
  return open(width, height) +
    `<defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">
       <stop offset="0%" stop-color="${color}" stop-opacity=".42"/>
       <stop offset="100%" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
     <line class="grid-line" x1="${m.l}" x2="${width - m.r}" y1="${m.t + ih}" y2="${m.t + ih}"/>
     <path d="${area}" fill="url(#ag)"/>
     <path d="${line}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round"/>
     <circle cx="${X(values.length - 1)}" cy="${Y(values[values.length - 1])}" r="4"
       fill="${color}" stroke="#fffdf4" stroke-width="2"/>
     <text class="axis-text" x="${m.l}" y="${height - 3}">payments processed \u2192</text>
     <text class="axis-text" x="${width - m.r}" y="${height - 3}" text-anchor="end">${
       esc(rupeeShort(values[values.length - 1]))} expected recovery</text></svg>`;
}

function scatterChart(points, { width = 420, height = 300 } = {}) {
  const m = { l: 40, r: 14, t: 12, b: 32 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const hi = Math.max(...points.flatMap((p) => [p.x, p.y]), 0.1) * 1.08;
  const X = (v) => m.l + (v / hi) * iw, Y = (v) => m.t + ih - (v / hi) * ih;
  let s = open(width, height);
  s += `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(hi)}" y2="${Y(hi)}" stroke-dasharray="4 4" stroke="rgba(40,54,24,.28)"/>
        <text class="axis-text" x="${X(hi) - 4}" y="${Y(hi) - 8}" text-anchor="end">ideal</text>
        <line class="grid-line" x1="${m.l}" x2="${m.l}" y1="${m.t}" y2="${m.t + ih}"/>
        <line class="grid-line" x1="${m.l}" x2="${width - m.r}" y1="${m.t + ih}" y2="${m.t + ih}"/>`;
  s += `<path d="${points.map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ")}"
          fill="none" stroke="${C.terracotta}" stroke-width="2"/>`;
  points.forEach((p) => {
    s += `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="3.6" fill="${C.terracotta}" stroke="#fffdf4" stroke-width="1.4"/>`;
  });
  s += `<text class="axis-text" x="${m.l + iw / 2}" y="${height - 6}" text-anchor="middle">what the model promised</text></svg>`;
  return s;
}

/* ============================== ledger ============================= */
async function loadLedger() {
  const d = await api("/api/ledger");
  const rev = d.policies.revenant, leg = d.policies.legacy_retry_T30;

  $("hero-batch").textContent = nfmt(d.batch_size);
  countUp($("s-recovered"), rev.recovered, rupeeShort);
  $("s-ci").textContent = `between ${rupeeShort(rev.ci95[0])} and ${rupeeShort(rev.ci95[1])} with 95% confidence`;

  countUp($("s-uplift"), d.uplift_pct, (v) => "+" + pct(v));
  $("s-uplift-abs").textContent = `${rupeeShort(d.uplift_vs_legacy)} more than trying again every 30 minutes`;

  countUp($("s-attempts"), d.attempt_reduction_pct, (v) => "\u2212" + pct(v));
  $("s-attempts-sub").textContent = `${nfmt(rev.actions)} attempts instead of ${nfmt(leg.actions)} — more money, less pestering`;

  countUp($("s-share"), rev.share_of_exposure, (v) => pct(v));
  $("s-share-sub").textContent = `of ${rupeeShort(d.exposure)} that was about to be lost`;

  $("ledger-chart").innerHTML = barChart([
    { label: "Do nothing", value: d.policies.do_nothing.recovered, color: "#6b5d4f",
      display: rupeeShort(d.policies.do_nothing.recovered), note: "every failed payment abandoned" },
    { label: "Fixed 30-min retry", value: leg.recovered, color: "#96826a", ci: leg.ci95,
      display: rupeeShort(leg.recovered), note: `${nfmt(leg.actions)} attempts \u00b7 what most businesses do today` },
    { label: "Revenant", value: rev.recovered, color: C.terracotta, ci: rev.ci95,
      display: rupeeShort(rev.recovered), note: `${nfmt(rev.actions)} attempts \u00b7 safety rules enforced` },
  ]);

  const row = (name, p, hl) => `<tr class="${hl ? "highlight" : ""}"><td>${name}</td>
    <td>${rupee(p.recovered)}</td><td>${pct(p.recovery_rate, 2)}</td><td>${nfmt(p.actions)}</td>
    <td>${nfmt(p.nudges)}</td><td>${nfmt(p.blocked)}</td><td>${nfmt(p.escalated)}</td></tr>`;
  $("ledger-table").innerHTML = `<table><thead><tr>
      <th>Approach</th><th>Recovered</th><th>Payments saved</th><th>Attempts</th>
      <th>Messages sent</th><th>Stopped by rules</th><th>Sent to a person</th></tr></thead><tbody>
      ${row("Do nothing", d.policies.do_nothing)}
      ${row("Fixed 30-min retry", leg)}
      ${row("Revenant", rev, true)}</tbody></table>`;
}

/* ============================== curves ============================= */
async function loadCurves() {
  const d = await api("/api/curve-by-reason?amount=2500&hour=20");
  const series = d.series.map((s) => ({
    name: s.reason, color: REASON_COLOR[s.reason] || C.navy,
    points: s.points.map((p) => ({ x: p.delay_min, y: p.p_success })),
  }));
  const ticks = d.series[0].points.map((p) => fmtDelay(p.delay_min));
  $("curve-chart").innerHTML = lineChart(series, { xTicks: ticks, yLabel: "chance of success" });
  $("curve-legend").innerHTML = series.map((s) =>
    `<span class="legend-item"><span class="legend-swatch" style="background:${s.color}"></span>${
      esc(REASON_PLAIN[s.name] || s.name)}</span>`).join("");

  const peak = (name) => {
    const s = series.find((x) => x.name === name);
    return s ? s.points.reduce((a, b) => (b.y > a.y ? b : a)) : null;
  };
  const drop = peak("network_drop"), funds = peak("insufficient_funds");
  if (drop && funds) {
    $("curve-callout").innerHTML =
      `A dropped connection is best retried after <strong>${fmtDelay(drop.x)}</strong> and is nearly worthless an hour later. ` +
      `An empty account is best retried after <strong>${fmtDelay(funds.x)}</strong> and is nearly worthless immediately. ` +
      `Revenant learned this from data on its own — waiting time turned out to be the single most useful thing the model looks at.`;
  }
}

/* ============================== console ============================ */
const state = {
  records: [], selected: null, cumulative: [0],
  counts: { total: 0, actions: 0, nudges: 0, blocked: 0, escalated: 0, agent: 0, ev: 0 },
};

function renderCounters() {
  const c = state.counts;
  const I = {
    seen: '<circle cx="10" cy="10" r="3"/><path d="M1.5 10S4.8 4 10 4s8.5 6 8.5 6-3.3 6-8.5 6-8.5-6-8.5-6z"/>',
    act: '<path d="M11 2L4 11h5l-1 7 7-9h-5l1-7z"/>',
    msg: '<path d="M17 12a2 2 0 01-2 2H7l-4 3V5a2 2 0 012-2h10a2 2 0 012 2v7z"/>',
    stop: '<circle cx="10" cy="10" r="7"/><path d="M5.5 5.5l9 9"/>',
    person: '<circle cx="10" cy="7" r="3"/><path d="M4 17a6 6 0 0112 0"/>',
    money: '<path d="M10 3v14M13.5 6.5H8.2a2.3 2.3 0 000 4.6h3.6a2.3 2.3 0 010 4.6H6"/>',
  };
  const items = [
    ["Payments seen", nfmt(c.total), I.seen], ["Actions taken", nfmt(c.actions), I.act],
    ["Customers messaged", nfmt(c.nudges), I.msg], ["Stopped by rules", nfmt(c.blocked), I.stop],
    ["Sent to a person", nfmt(c.escalated), I.person], ["Expected recovery", rupeeShort(c.ev), I.money],
  ];
  $("counters").innerHTML = items.map(([k, v, ico]) =>
    `<div class="counter"><div class="k">
       <svg class="ico" style="width:12px;height:12px" viewBox="0 0 20 20" aria-hidden="true">${ico}</svg>${k}</div>
     <div class="v">${v}</div></div>`).join("");
  if (state.cumulative.length > 1) $("live-chart").innerHTML = areaChart(state.cumulative);
}

function actionLine(r) {
  const a = r.final_action;
  const base = STRATEGY_PLAIN[a.strategy] || a.strategy;
  if (a.strategy === "no_action" || a.strategy === "escalate_human") return base;
  const when = a.delay_min <= 5 ? "now" : "in " + fmtDelay(a.delay_min);
  const via = a.channel === "none" ? "" : ` by ${a.channel}`;
  return `${base} — ${when}${via}`;
}

function renderCard(r) {
  const el = document.createElement("div");
  el.className = `card v-${r.guard_verdict}`;
  el.dataset.txn = r.txn_id;
  el.setAttribute("role", "button");
  el.innerHTML = `
    <div class="card-top">
      <span class="card-amt">${rupee(r.amount)}</span>
      <span class="card-reason">${esc(REASON_PLAIN[r.reason] || r.reason)}</span>
    </div>
    <div class="card-action">${esc(actionLine(r))}</div>
    <div class="card-meta">
      <span class="badge b-${r.guard_verdict}">${VERDICT_PLAIN[r.guard_verdict] || r.guard_verdict}</span>
      <span class="badge b-src">${r.decision_source === "llm_agent" ? "AI agent" : "model"}</span>
      <span class="badge b-src">${r.latency_ms} ms</span>
    </div>`;
  el.addEventListener("click", () => select(r.txn_id));
  return el;
}

const VERDICT_PLAIN = { PASS: "ALLOWED", MODIFY: "ADJUSTED", BLOCK: "STOPPED", ESCALATE: "TO A PERSON" };

function select(txnId) {
  state.selected = txnId;
  document.querySelectorAll(".card").forEach((c) => c.classList.toggle("selected", c.dataset.txn === txnId));
  const r = state.records.find((x) => x.txn_id === txnId);
  if (r) renderDetail(r);
}

function renderDetail(r) {
  const maxEv = Math.max(...r.top_candidates.map((c) => c.expected_value), 1);
  const cands = r.top_candidates.map((c, i) => `
    <div class="cand ${i === 0 ? "top" : ""}">
      <div class="cand-row">
        <span><strong>${esc(STRATEGY_PLAIN[c.strategy] || c.strategy)}</strong>
          ${c.delay_min <= 5 ? "now" : "in " + fmtDelay(c.delay_min)}${
          c.channel === "none" ? "" : " \u00b7 " + c.channel}</span>
        <span>${pct(c.p_success, 0)} \u00b7 worth ${rupee(c.expected_value)}</span>
      </div>
      <div class="cand-bar"><div class="cand-fill" style="width:${Math.max(2, (c.expected_value / maxEv) * 100)}%"></div></div>
    </div>`).join("");

  const notes = r.guard_notes.length
    ? r.guard_notes.map((n) => `<div class="step-v">${esc(n)}</div>`).join("")
    : `<div class="step-v">No rule was triggered — the action ran exactly as chosen.</div>`;

  $("detail").innerHTML = `
    <h3>${rupee(r.amount)} \u00b7 ${esc(REASON_PLAIN[r.reason] || r.reason)}</h3>
    <div class="sub">Failed at ${String(r.hour).padStart(2, "0")}:00 \u00b7 attempt ${r.prior_attempts + 1}</div>
    <div class="rationale">${esc(r.rationale || "")}</div>
    <ul class="chain">
      <li><div class="step-k">1 — Every option was scored</div>
        <div class="step-v">${r.candidates_evaluated} possible actions were compared. The best five:</div>
        <div style="margin-top:10px">${cands}</div></li>
      <li><div class="step-k">2 — Who decided</div>
        <div class="step-v">${r.decision_source === "llm_agent"
          ? "The top two options were almost equally valuable, so an AI agent picked between them based on what is kinder to the customer."
          : "One option was clearly best, so the model decided directly — no AI call was needed."}</div></li>
      <li class="step-guard"><div class="step-k">3 — Safety check: ${
        VERDICT_PLAIN[r.guard_verdict] || r.guard_verdict}</div>${notes}</li>
      <li><div class="step-k">4 — What actually happened</div>
        <div class="step-v">${esc(actionLine(r))}</div></li>
    </ul>
    <dl class="kv">
      <dt>Recorded</dt><dd>${esc(new Date(r.ts).toLocaleString("en-IN"))}</dd>
      <dt>Payment method</dt><dd>${esc(r.method)} \u00b7 ${esc(r.bank)}</dd>
      <dt>Originally chosen</dt><dd>${esc(STRATEGY_PLAIN[r.proposed_action.strategy] || r.proposed_action.strategy)}${
        r.proposed_action.delay_min > 5 ? ", in " + fmtDelay(r.proposed_action.delay_min) : ""}</dd>
      <dt>Actually done</dt><dd>${esc(STRATEGY_PLAIN[r.final_action.strategy] || r.final_action.strategy)}${
        r.final_action.delay_min > 5 ? ", in " + fmtDelay(r.final_action.delay_min) : ""}</dd>
      <dt>Time to decide</dt><dd>${r.latency_ms} ms</dd>
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
  state.cumulative.push(c.ev);
}

async function runBatch(n = 24) {
  const btn = $("btn-run");
  btn.disabled = true;
  const label = btn.innerHTML;
  btn.innerHTML = "Running\u2026";
  const feed = $("feed");
  try {
    const { transactions } = await api(`/api/sample?n=${n}`);
    if (feed.querySelector(".empty")) feed.innerHTML = "";
    for (const t of transactions) {
      let rec;
      try {
        rec = await api("/api/analyze", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(t),
        }, 30000);
      } catch (e) {
        console.warn("skipped one payment:", e.message);
        continue;                       // one bad payment never stops the stream
      }
      state.records.unshift(rec);
      tally(rec);
      feed.prepend(renderCard(rec));
      renderCounters();
      if (!state.selected) select(rec.txn_id);
      await new Promise((r) => setTimeout(r, 170));
    }
  } catch (e) {
    feed.innerHTML = `<div class="empty">Could not reach the server — ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = label;
  }
}

/* ============================== copilot ============================ */
const CHIPS = [
  "How much did we recover versus the old system?",
  "Why are we blocking payments?",
  "What would a 5-retry limit be worth?",
  "What are the quiet hours costing us?",
  "How are we doing overall?",
];

const SOURCE_LABEL = {
  policy_whatif: "Ran a simulation",
  decision_summary: "Reviewed recent decisions",
  find_decisions: "Examined individual payments",
  ledger: "Checked the scoreboard",
  rulebook: "Checked the safety rules",
  model_quality: "Checked model accuracy",
  failure_playbook: "Consulted the failure guide",
};

function thinkingLabel(q) {
  const l = q.toLowerCase();
  if (/(what if|if we|worth|cap|limit|costing|quiet hours|retries|retry)/.test(l))
    return "Running a simulation over 600 payments\u2026";
  return "Looking that up\u2026";
}

function copilotTurn(question) {
  const log = $("copilot-log");
  if (log.querySelector(".empty")) log.innerHTML = "";
  const el = document.createElement("div");
  el.className = "turn";
  el.innerHTML = `<div class="turn-q"><b>YOU</b>${esc(question)}</div>
    <div class="thinking"><i></i><i></i><i></i><span>${esc(thinkingLabel(question))}</span></div>`;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

async function askCopilot(question) {
  const q = question.trim();
  if (!q) return;
  const input = $("copilot-input"), btn = $("copilot-send");
  input.value = "";
  btn.disabled = true;
  const turn = copilotTurn(q);
  try {
    const r = await api("/api/copilot", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    }, 60000);
    const seen = new Set();
    const sources = (r.tool_calls || [])
      .map((c) => SOURCE_LABEL[c.tool])
      .filter((l) => l && !seen.has(l) && seen.add(l))
      .map((l) => `<span class="source-tag">
        <svg class="ico" style="width:12px;height:12px" viewBox="0 0 20 20" aria-hidden="true">
          <path d="M4 10.5l4 4 8-9"/></svg>${esc(l)}</span>`).join("");
    turn.innerHTML = `<div class="turn-q"><b>YOU</b>${esc(q)}</div>
      <div class="turn-a">${esc(r.answer)}</div>
      ${sources ? `<div class="source-strip">${sources}</div>` : ""}`;
  } catch (e) {
    turn.innerHTML = `<div class="turn-q"><b>YOU</b>${esc(q)}</div>
      <div class="turn-a">I could not complete that lookup (${esc(e.message)}). Try one of the
      suggested questions above.</div>`;
  } finally {
    btn.disabled = false;
    $("copilot-log").scrollTop = $("copilot-log").scrollHeight;
  }
}

function initCopilot() {
  $("copilot-chips").innerHTML = CHIPS.map((c) => `<button class="chip">${esc(c)}</button>`).join("");
  $("copilot-chips").querySelectorAll(".chip").forEach((b) =>
    b.addEventListener("click", () => askCopilot(b.textContent)));
  $("copilot-send").addEventListener("click", () => askCopilot($("copilot-input").value));
  $("copilot-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") askCopilot($("copilot-input").value);
  });
}

/* ============================== sandbox ============================ */
const sandbox = { schema: null, values: {} };
const STEP = { MAX_ATTEMPTS: 1, MIN_SPACING_MIN: 5, DND_START: 1, DND_END: 1, HUMAN_REVIEW_AMOUNT: 500 };

function sliderDisplay(key, v) {
  if (key === "HUMAN_REVIEW_AMOUNT") return rupee(v);
  if (key === "MIN_SPACING_MIN") return v + " min";
  if (key === "DND_START" || key === "DND_END") return String(v).padStart(2, "0") + ":00";
  return v + (v === 1 ? " try" : " tries");
}

async function loadSandbox() {
  const s = await api("/api/whatif/defaults");
  sandbox.schema = s;
  sandbox.values = { ...s.defaults };
  $("sliders").innerHTML = Object.entries(s.tunable).map(([k, meta]) => `
    <div class="slider-row">
      <div class="slider-head">
        <span class="slider-name">${esc(meta.label)}</span>
        <span class="slider-val" id="val-${k}">${esc(sliderDisplay(k, s.defaults[k]))}</span>
      </div>
      <input type="range" id="rng-${k}" min="${meta.range[0]}" max="${meta.range[1]}"
             step="${STEP[k] || 1}" value="${s.defaults[k]}" />
      <div class="slider-note">${esc(meta.help)}</div>
    </div>`).join("");

  Object.keys(s.tunable).forEach((k) => {
    $(`rng-${k}`).addEventListener("input", (e) => {
      sandbox.values[k] = Number(e.target.value);
      $(`val-${k}`).textContent = sliderDisplay(k, sandbox.values[k]);
    });
  });

  $("locked-note").innerHTML = s.locked.map((l) => `<div class="locked">
      <svg class="ico" viewBox="0 0 20 20" aria-hidden="true"><rect x="4.5" y="9" width="11" height="7.5" rx="1.6"/><path d="M7 9V6.6a3 3 0 016 0V9"/></svg>
      ${esc(l.text)} This cannot be switched off.</div>`).join("");
}

async function runWhatif() {
  const btn = $("btn-whatif"), out = $("whatif-result");
  const changed = {};
  Object.entries(sandbox.values).forEach(([k, v]) => {
    if (v !== sandbox.schema.defaults[k]) changed[k] = v;
  });
  if (!Object.keys(changed).length) {
    out.innerHTML = `<div class="empty">Move at least one setting away from its current value first.</div>`;
    return;
  }
  btn.disabled = true;
  const label = btn.innerHTML;
  btn.innerHTML = "Simulating\u2026";
  out.innerHTML = `<div class="empty"><span class="thinking"><i></i><i></i><i></i></span><br><br>
    Replaying ${nfmt(sandbox.schema.subsample)} payments with your new setting\u2026</div>`;
  try {
    const r = await api("/api/whatif", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changed),
    }, 100000);
    const d = r.delta_revenue;
    const cls = Math.abs(r.delta_revenue_pct) < 0.005 ? "flat" : (d > 0 ? "up" : "down");
    const verb = cls === "flat" ? "No real change" : (d > 0 ? "Gains" : "Costs");
    out.innerHTML = `
      <div class="wi-head ${cls}">${d > 0 ? "+" : d < 0 ? "\u2212" : ""}${esc(rupeeShort(Math.abs(d)))}</div>
      <div class="wi-sub">${verb} \u00b7 ${pct(r.delta_revenue_pct, 1)} versus your current settings,
        measured over ${nfmt(r.subsample)} payments \u00b7 ${esc(r.changed_label)}</div>
      <div class="wi-grid">
        <div class="wi-cell"><div class="k">Recovered now</div><div class="v">${esc(rupeeShort(r.baseline.recovered))}</div></div>
        <div class="wi-cell"><div class="k">Recovered after</div><div class="v">${esc(rupeeShort(r.scenario.recovered))}</div></div>
        <div class="wi-cell"><div class="k">Attempts</div><div class="v">${r.delta_actions >= 0 ? "+" : ""}${nfmt(r.delta_actions)}</div></div>
        <div class="wi-cell"><div class="k">Messages</div><div class="v">${r.delta_nudges >= 0 ? "+" : ""}${nfmt(r.delta_nudges)}</div></div>
      </div>
      ${r.revenue_per_extra_attempt && d > 0
        ? `<div class="wi-note">Each extra attempt is worth about ${esc(rupee(r.revenue_per_extra_attempt))}.</div>` : ""}
      <div class="wi-note">${esc(r.note)}</div>`;
  } catch (e) {
    out.innerHTML = `<div class="empty">Could not run that scenario — ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = label;
  }
}

function resetSandbox() {
  if (!sandbox.schema) return;
  sandbox.values = { ...sandbox.schema.defaults };
  Object.entries(sandbox.values).forEach(([k, v]) => {
    const r = $(`rng-${k}`);
    if (r) { r.value = v; $(`val-${k}`).textContent = sliderDisplay(k, v); }
  });
  $("whatif-result").innerHTML = `<div class="empty">Adjust a setting, then run the scenario.</div>`;
}

/* ============================ rulebook / model ===================== */
async function loadRulebook() {
  const d = await api("/api/rulebook");
  const shield = '<path d="M10 2.5l6 2.2v5c0 3.6-2.5 6.6-6 7.8-3.5-1.2-6-4.2-6-7.8v-5l6-2.2z"/><path d="M7.4 10l1.9 1.9 3.4-3.6"/>';
  $("rulebook").innerHTML = d.rules.map((r) =>
    `<div class="rule">
       <svg class="ico" viewBox="0 0 20 20" aria-hidden="true">${shield}</svg>
       <div class="rule-text">${esc(r.text.replace(/^Rs /, "\u20B9"))}</div></div>`).join("");
}

async function loadModelCard() {
  const m = await api("/api/model-card");
  const metric = (k, v, n) => `<div class="metric"><div class="k">${k}</div>
    <div class="v">${v}</div><div class="n">${n}</div></div>`;
  $("model-metrics").innerHTML = [
    metric("Ranking accuracy", m.auc.toFixed(3),
           m.cv ? `${m.cv.auc_mean.toFixed(3)} \u00b1 ${m.cv.auc_std.toFixed(3)} across ${m.cv.folds} folds`
                : `tested on ${nfmt(m.n_holdout)} unseen payments`),
    metric("Skill vs. guessing", m.brier_skill_score != null
             ? (100 * m.brier_skill_score).toFixed(1) + "%" : m.brier.toFixed(3),
           "better than always predicting the average"),
    metric("Promised", m.mean_predicted.toFixed(3), "average success the model predicted"),
    metric("Delivered", m.base_rate.toFixed(3), "what actually happened — the two should match"),
  ].join("");
  $("calib-chart").innerHTML = scatterChart(m.calibration.map((c) => ({ x: c.predicted, y: c.observed })));
  const top = m.feature_importance.slice(0, 7);
  const PLAIN = { log_delay: "How long we wait", reason: "Why it failed", amount: "Payment size",
    hour: "Time of day", strategy: "What we try", bank: "Which bank", channel: "Message channel",
    prior_attempts: "Tries so far", method: "Payment method", device: "Device", network_quality: "Connection" };
  $("gain-chart").innerHTML = barChart(top.map((f, i) => ({
    label: PLAIN[f.feature] || f.feature, value: f.gain,
    color: i === 0 ? C.terracotta : C.ocean,
    display: ((f.gain / top[0].gain) * 100).toFixed(0) + "%",
  })), { width: 440, rowH: 34, pad: 150, valW: 60 });
}

/* ============================== boot =============================== */
async function boot() {
  initCursor();
  initReveal();
  initCopilot();

  const h = $("health");
  try {
    const s = await api("/api/health", {}, 10000);
    const online = s.effective === "online";
    h.className = online ? "pill pill-ok" : "pill pill-bad";
    h.innerHTML = `<span class="dot"></span>${online
      ? "Connected \u00b7 full AI answers" : "Connected \u00b7 built-in answers"}`;
    const note = $("mode-note");
    if (note) {
      note.innerHTML = online
        ? `<svg class="ico ico-inline" viewBox="0 0 20 20"><path d="M4 10.5l4 4 8-9"/></svg>
           Full AI answering is on via ${esc(s.provider)} — ask anything in your own words.`
        : `<svg class="ico ico-inline" viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5"/><path d="M10 6.5v4M10 13.5v.01"/></svg>
           <span>Running on built-in answers${s.problem ? " — " + esc(s.problem) : ""}.
           Add <code>GROQ_API_KEY</code> to a <code>.env</code> file in the project folder
           and restart the server.</span>`;
    }
  } catch {
    h.className = "pill pill-bad";
    h.innerHTML = `<span class="dot"></span>Server offline`;
  }

  const jobs = [
    [loadLedger, "ledger-chart", "Run the evaluate step first, then refresh."],
    [loadCurves, "curve-chart", "Train the model first, then refresh."],
    [loadRulebook, "rulebook", "Server offline."],
    [loadSandbox, "sliders", "Server offline."],
    [loadModelCard, "model-metrics", "Train the model first, then refresh."],
  ];
  for (const [fn, target, msg] of jobs) {
    try { await fn(); } catch (e) {
      console.warn(target, e);
      $(target).innerHTML = `<div class="empty">${esc(msg)}</div>`;
    }
  }

  renderCounters();
  $("btn-run").addEventListener("click", () => runBatch());
  $("btn-whatif").addEventListener("click", runWhatif);
  $("btn-whatif-reset").addEventListener("click", resetSandbox);
  $("btn-clear").addEventListener("click", () => {
    state.records = []; state.selected = null; state.cumulative = [0];
    state.counts = { total: 0, actions: 0, nudges: 0, blocked: 0, escalated: 0, agent: 0, ev: 0 };
    $("feed").innerHTML = `<div class="empty">Press <strong>Run payments</strong> to begin.</div>`;
    $("detail").innerHTML = `<div class="empty">Select a card on the left.</div>`;
    $("live-chart").innerHTML = "";
    renderCounters();
  });
}

document.addEventListener("DOMContentLoaded", boot);
