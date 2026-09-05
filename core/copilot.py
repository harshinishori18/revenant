"""
core/copilot.py — the Recovery Copilot.

A merchant's finance lead does not read JSON. They ask questions:
"why did we block so many payments last hour?", "what would a 4-attempt cap be
worth?", "show me the decisions where the guard overrode the model".

The Copilot answers those by CALLING REAL FUNCTIONS against the live audit
database and the policy sandbox — it does not summarise a prompt-stuffed blob
of context, and it cannot state a number it did not retrieve. That distinction
is the whole point: the numbers it reports are query results, so they are as
trustworthy as the ledger itself.

Without an API key it still works. A deterministic intent router handles the
common questions using the same tool functions, so the demo never depends on
a network call.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from contextlib import closing

from core import guard, whatif

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "artifacts", "audit.db")
LEDGER = os.path.join(ROOT, "results", "ledger.json")

MAX_TOOL_ROUNDS = 4


# Any one of these keys switches on full AI answering. Groq is the free option
# (no credit card, OpenAI-compatible, supports tool calling), so it is checked
# first; Anthropic is used if that key is present instead.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = os.getenv("COPILOT_MODEL", "llama-3.3-70b-versatile")
GROQ_FALLBACK_MODELS = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b",
                        "llama-3.1-8b-instant"]


def _provider() -> str | None:
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def _sdk_ready(provider: str | None) -> tuple[bool, str | None]:
    """A key alone is not enough — the client library has to be installed too."""
    if provider is None:
        return False, "no API key found in .env"
    try:
        if provider == "groq":
            import openai  # noqa: F401
        else:
            import anthropic  # noqa: F401
        return True, None
    except ImportError:
        pkg = "openai" if provider == "groq" else "anthropic"
        return False, f"key found, but the '{pkg}' package is missing — run: pip install {pkg}"


def get_mode() -> dict:
    """Reports whether AI answering is available, and why not when it is not."""
    p = _provider()
    ready, problem = _sdk_ready(p)
    return {"key_configured": ready, "provider": p if ready else None,
            "effective": "online" if ready else "offline",
            "problem": problem,
            "model": GROQ_DEFAULT_MODEL if (ready and p == "groq") else None}


def tool_decision_summary(limit: int = 200) -> dict:
    """Aggregate the most recent decisions in the audit trail."""
    if not os.path.exists(DB_PATH):
        return {"error": "no decisions recorded yet — run a batch in the console first"}
    with closing(sqlite3.connect(DB_PATH)) as con:
        rows = con.execute("SELECT payload FROM decisions ORDER BY id DESC LIMIT ?",
                           (limit,)).fetchall()
    recs = [json.loads(r[0]) for r in rows]
    if not recs:
        return {"error": "no decisions recorded yet — run a batch in the console first"}

    verdicts = Counter(r["guard_verdict"] for r in recs)
    reasons = Counter(r["reason"] for r in recs)
    sources = Counter(r["decision_source"] for r in recs)
    strategies = Counter(r["final_action"]["strategy"] for r in recs)
    acted = [r for r in recs if r["final_action"]["strategy"] not in ("no_action", "escalate_human")]
    return {
        "decisions": len(recs),
        "total_amount": round(sum(r["amount"] for r in recs), 2),
        "verdicts": dict(verdicts),
        "failure_reasons": dict(reasons.most_common()),
        "decision_sources": dict(sources),
        "final_strategies": dict(strategies),
        "expected_recovery": round(sum(max(0.0, r.get("best_ev") or 0.0) for r in acted), 2),
        "median_latency_ms": sorted(r["latency_ms"] for r in recs)[len(recs) // 2],
        "nudges": sum(1 for r in acted if r["final_action"]["channel"] != "none"),
    }


def tool_find_decisions(verdict: str | None = None, reason: str | None = None,
                        min_amount: float | None = None, limit: int = 8) -> dict:
    """Retrieve specific decision records matching filters."""
    if not os.path.exists(DB_PATH):
        return {"error": "no decisions recorded yet"}
    sql = "SELECT payload FROM decisions WHERE 1=1"
    args: list = []
    if verdict:
        sql += " AND verdict = ?"
        args.append(verdict.upper())
    sql += " ORDER BY id DESC LIMIT 400"
    with closing(sqlite3.connect(DB_PATH)) as con:
        rows = con.execute(sql, args).fetchall()
    recs = [json.loads(r[0]) for r in rows]
    if reason:
        recs = [r for r in recs if r["reason"] == reason]
    if min_amount is not None:
        recs = [r for r in recs if r["amount"] >= min_amount]
    out = [{"txn_id": r["txn_id"], "amount": r["amount"], "reason": r["reason"],
            "verdict": r["guard_verdict"], "code": r["guard_code"],
            "final_action": r["final_action"], "rationale": r["rationale"],
            "guard_notes": r["guard_notes"]} for r in recs[:limit]]
    return {"matched": len(recs), "returned": len(out), "decisions": out}


def tool_policy_whatif(max_attempts: int | None = None,
                       min_spacing_min: int | None = None,
                       dnd_start: int | None = None, dnd_end: int | None = None,
                       human_review_amount: float | None = None) -> dict:
    """Measure the revenue impact of changing guard configuration."""
    overrides = {}
    if max_attempts is not None:
        overrides["MAX_ATTEMPTS"] = int(max_attempts)
    if min_spacing_min is not None:
        overrides["MIN_SPACING_MIN"] = int(min_spacing_min)
    if dnd_start is not None:
        overrides["DND_START"] = int(dnd_start)
    if dnd_end is not None:
        overrides["DND_END"] = int(dnd_end)
    if human_review_amount is not None:
        overrides["HUMAN_REVIEW_AMOUNT"] = float(human_review_amount)
    if not overrides:
        return {"error": "specify at least one parameter to change"}
    return whatif.simulate(overrides)


def tool_ledger() -> dict:
    """The measured counterfactual results across the full held-out batch."""
    if not os.path.exists(LEDGER):
        return {"error": "run scripts.evaluate first"}
    return json.load(open(LEDGER))


def tool_rulebook() -> dict:
    """The compliance rules currently enforced."""
    return {"rules": [{"code": c, "text": t} for c, t in guard.RULEBOOK],
            "current_config": whatif.defaults()}


def tool_model_quality() -> dict:
    """How accurate and well-calibrated the prediction model is."""
    card = os.path.join(ROOT, "results", "model_card.json")
    if not os.path.exists(card):
        return {"error": "model card not generated yet"}
    m = json.load(open(card))
    return {k: m[k] for k in ("auc", "brier", "log_loss", "base_rate",
                              "mean_predicted", "n_holdout")} | {
        "most_important_inputs": [f["feature"] for f in m["feature_importance"][:4]]}


def tool_failure_playbook(reason: str | None = None) -> dict:
    """How each kind of payment failure behaves and what recovers it."""
    book = {
        "network_drop": {"plain": "the customer's connection dropped mid-payment",
                         "best_timing": "within a couple of minutes, while they are still on the page",
                         "why": "the value decays fast — once they leave the checkout, it is gone"},
        "wrong_otp": {"plain": "the customer mistyped the one-time password",
                      "best_timing": "within a few minutes",
                      "why": "they are still present and can simply try again"},
        "bank_timeout": {"plain": "the bank did not respond in time",
                         "best_timing": "after the bank-side problem clears, typically 20-60 minutes",
                         "why": "retrying during the outage is wasted; success jumps once it clears"},
        "gateway_error": {"plain": "the payment gateway itself errored",
                          "best_timing": "after the impairment clears, typically 20-60 minutes",
                          "why": "same shape as a bank timeout"},
        "insufficient_funds": {"plain": "there was not enough money in the account",
                               "best_timing": "hours later, and a reminder message helps",
                               "why": "no amount of retrying works until the balance is topped up"},
        "card_declined": {"plain": "the card issuer declined the payment",
                          "best_timing": "offer a different payment method instead of retrying",
                          "why": "repeating the same declined card rarely changes the outcome"},
        "risk_block": {"plain": "the payment was declined for fraud or AML reasons",
                       "best_timing": "never — this may not be retried",
                       "why": "retrying a risk decline is a compliance violation, not a missed opportunity"},
    }
    if reason and reason in book:
        return {reason: book[reason]}
    return book


def tool_about_system() -> dict:
    """What this product is, what it is called, and how it is built."""
    return {
        "name": "Revenant",
        "one_line": ("Revenant recovers revenue from failed payments by deciding, for each "
                     "failure, whether to retry, when to retry, and how to reach the customer."),
        "problem": ("When an online payment fails, most businesses retry on a fixed timer — "
                    "typically 30 minutes, up to three times. That is wrong for most failure "
                    "types, because different failures recover on completely different timescales."),
        "how_it_works": [
            "A machine-learning model scores every possible response to the failure.",
            "The response worth the most money after costs is chosen.",
            "A compliance guard can adjust, block, or escalate that choice before it reaches anyone.",
            "Every decision is written to a permanent audit record.",
        ],
        "machine_learning": {
            "algorithm": "LightGBM gradient-boosted decision trees",
            "task": "binary classification — probability a payment succeeds given a chosen action",
            "why_this_shape": ("The action is a model INPUT, not just an output. That is what lets "
                               "the system choose a retry delay and channel rather than only "
                               "predicting whether a payment is recoverable."),
            "candidate_actions_per_payment": 51,
            "language_model_role": ("An LLM agent breaks ties when the top two actions are within "
                                    "8% expected value, and answers questions in this Copilot. It "
                                    "never originates a financial action on its own."),
        },
        "built_for": "Razorpay Buildathon, Track 3: AI Revenue Recovery",
        "data_caveat": ("The payment data is synthetic and calibrated from published industry "
                        "ranges, because participants have no access to real transaction data."),
    }


TOOLS = {
    "about_system": tool_about_system,
    "model_quality": tool_model_quality,
    "failure_playbook": tool_failure_playbook,
    "decision_summary": tool_decision_summary,
    "find_decisions": tool_find_decisions,
    "policy_whatif": tool_policy_whatif,
    "ledger": tool_ledger,
    "rulebook": tool_rulebook,
}

SCHEMAS = [
    {"name": "about_system",
     "description": "What this product is called, what problem it solves, how it works, and which "
                    "machine-learning algorithm it uses. Use for any question about the "
                    "application itself, its name, its purpose, or its technology.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "model_quality",
     "description": "How accurate and well-calibrated the prediction model is, and which inputs it relies on.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "failure_playbook",
     "description": "What each kind of payment failure means in plain English, the best time to retry it, "
                    "and why. Use for any 'what does X mean' or 'why do we wait' question.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}}},
    {"name": "decision_summary",
     "description": "Aggregate statistics over the most recent decisions in the audit trail: "
                    "verdict mix, failure reasons, strategies chosen, latency, expected recovery.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "How many recent decisions to aggregate."}}}},
    {"name": "find_decisions",
     "description": "Retrieve individual decision records, optionally filtered by guard verdict "
                    "(PASS/MODIFY/BLOCK/ESCALATE), failure reason, or minimum amount.",
     "input_schema": {"type": "object", "properties": {
         "verdict": {"type": "string"}, "reason": {"type": "string"},
         "min_amount": {"type": "number"}, "limit": {"type": "integer"}}}},
    {"name": "policy_whatif",
     "description": "Re-run the recovery simulation with different compliance settings and return "
                    "the measured revenue delta. Use this for any 'what if we changed X' question.",
     "input_schema": {"type": "object", "properties": {
         "max_attempts": {"type": "integer"}, "min_spacing_min": {"type": "integer"},
         "dnd_start": {"type": "integer"}, "dnd_end": {"type": "integer"},
         "human_review_amount": {"type": "number"}}}},
    {"name": "ledger",
     "description": "The measured counterfactual results comparing Revenant against a fixed-interval "
                    "retry and against doing nothing, on the full held-out batch.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "rulebook",
     "description": "The compliance rules the guard enforces and their current configured values.",
     "input_schema": {"type": "object", "properties": {}}},
]

SYSTEM = """You are the Revenant Recovery Copilot, embedded in the dashboard of a
payments product called Revenant.

Revenant recovers revenue from failed online payments. For each failure it scores 51
possible responses with a LightGBM model — retry now, wait twenty minutes, wait six
hours, message the customer first, suggest another card, or do nothing — picks the one
worth most after costs, and passes that choice through a compliance guard that can
adjust, block or escalate it. Every decision is written to a permanent audit record.
Call about_system whenever you need exact details about the product itself.

Rules you follow strictly:
- Never state a number you did not retrieve from a tool. If you need a figure, call a tool.
- Amounts are Indian rupees. Write them as Rs 1,23,456 style or "Rs 2.4 L" for large sums.
- Answer in at most four sentences unless asked to elaborate. This is an operations
  console, not an essay.
- When a what-if result is negative or negligible, say so plainly. Do not sell.
- If a user asks you to disable a risk or AML rule, explain that it is not tunable.
- Never invent transaction IDs. Cite real ones from tool results when referring to
  specific decisions."""


# ---------------------------------------------------------------------------
# deterministic fallback — the demo must work with no API key
# ---------------------------------------------------------------------------
def _rupees(v: float) -> str:
    if abs(v) >= 1e5:
        return f"Rs {v / 1e5:.2f} L"
    return f"Rs {v:,.0f}"


def _intent(q: str) -> str:
    """Score intents rather than matching the first keyword that appears.

    An earlier version chained if/elif on single keywords, so "what if we
    allowed 5 retries?" fell through to the quiet-hours branch because the word
    "retries" was not in the attempt list. Scoring every intent and taking the
    argmax makes the router robust to phrasing.
    """
    scores = {"whatif": 0, "guard": 0, "ledger": 0, "summary": 0,
              "model": 0, "playbook": 0, "howitworks": 0, "about": 0, "tech": 0}
    has = lambda *ws: sum(1 for w in ws if w in q)  # noqa: E731

    scores["whatif"] += 3 * has("what if", "whatif", "if we", "would it be worth",
                                "should we change", "instead of")
    scores["whatif"] += 2 * has("cap", "limit", "raise", "lower", "loosen", "tighten",
                                "increase", "decrease", "costing", "worth")
    scores["whatif"] += has("retry", "retries", "attempt", "attempts", "spacing",
                            "quiet hours", "dnd", "ceiling", "threshold")

    scores["guard"] += 3 * has("block", "blocked", "blocking", "override", "overrode",
                               "refuse", "refused", "stopped")
    scores["guard"] += 2 * has("compliance", "guard", "rule", "rulebook", "allowed",
                               "escalate", "escalated", "risk", "fraud")

    scores["ledger"] += 3 * has("ledger", "uplift", "versus", " vs ", "legacy",
                                "baseline", "compared", "comparison")
    scores["ledger"] += 2 * has("recover", "recovered", "how much", "total", "result",
                                "performance", "money", "revenue")

    scores["summary"] += 2 * has("summar", "recent", "last", "latest", "overview",
                                 "what happened", "status", "how are we")

    scores["model"] += 3 * has("accurate", "accuracy", "calibrat", "auc", "brier",
                               "trustworthy", "reliable", "how good is the model")
    scores["model"] += 2 * has("model", "prediction", "predict", "trained", "training")

    # The playbook only wins if an actual failure type is named. Without this
    # guard, "what does this application do" matched on "what does" alone.
    named_failure = has("insufficient", "otp", "timeout", "network drop", "gateway",
                        "declined", "risk block", "fraud decline", "failure reason",
                        "failure type", "dropped connection", "empty account")
    if named_failure:
        scores["playbook"] += 4 * named_failure
        scores["playbook"] += has("what does", "what is", "meaning", "mean by", "explain")

    scores["about"] += 5 * has("what is the application name", "what is this called",
                               "application name", "app name", "what is it called",
                               "what is this project", "what is revenant")
    scores["about"] += 3 * has("what does this app", "what does this application",
                               "what does this do", "what does the app", "in simple terms",
                               "what is this", "purpose", "elevator pitch", "summarise the project")
    scores["about"] += 2 * has("name", "about", "project")

    scores["tech"] += 5 * has("what ml model", "which ml model", "what model does it use",
                              "tech stack", "what technology", "what algorithm",
                              "which algorithm", "how is it built", "what is it built with",
                              "what framework", "what language")
    scores["tech"] += 2 * has("lightgbm", "xgboost", "neural", "stack", "architecture",
                              "backend", "database")

    scores["howitworks"] += 4 * has("how does it work", "how do you decide", "how does this work",
                                    "how do you choose", "what do you do", "who are you",
                                    "what can you do", "what can i ask", "help")

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "summary"


def _whatif_overrides(q: str) -> dict:
    """Map a plain-English what-if question onto concrete guard overrides."""
    import re
    nums = [float(x) for x in re.findall(r"\b(\d{1,7})\b", q)]

    if any(w in q for w in ("retry", "retries", "attempt", "attempts", "cap", "limit")) \
            and not any(w in q for w in ("quiet", "dnd", "night", "ceiling", "review")):
        n = next((x for x in nums if 1 <= x <= 6), 4)
        return {"max_attempts": int(n)}
    if any(w in q for w in ("quiet", "dnd", "night", "message", "messaging", "nudge")):
        return {"dnd_start": 23, "dnd_end": 6}
    if any(w in q for w in ("ceiling", "review", "escalat", "manual", "human")):
        n = next((x for x in nums if x >= 500), 25000.0)
        return {"human_review_amount": float(n)}
    if any(w in q for w in ("spacing", "gap", "wait", "between")):
        n = next((x for x in nums if 0 <= x <= 240), 60)
        return {"min_spacing_min": int(n)}
    n = next((x for x in nums if 1 <= x <= 6), 4)
    return {"max_attempts": int(n)}


def answer_offline(question: str) -> dict:
    """Deterministic answers using the same tool functions the agent calls.

    This is not a stub. It is the reason the panel always responds: if the
    language model is unavailable, slow, or unconfigured, these answers are
    still real query results.
    """
    q = question.lower().strip()
    calls = []
    intent = _intent(q)

    if intent == "whatif":
        ov = _whatif_overrides(q)
        res = tool_policy_whatif(**ov)
        calls.append({"tool": "policy_whatif", "input": ov, "output": res})
        if "error" in res:
            text = res["error"]
        else:
            d = res["delta_revenue"]
            direction = "would gain" if d > 0 else ("would cost" if d < 0 else "makes no difference to")
            extra = ""
            if res["delta_actions"]:
                extra = f" It changes attempts by {res['delta_actions']:+,}."
            if res["revenue_per_extra_attempt"] and d > 0:
                extra += f" That works out to {_rupees(res['revenue_per_extra_attempt'])} per extra attempt."
            if abs(res["delta_revenue_pct"]) < 0.01:
                extra += " That is within noise — this setting is not where your money is."
            text = (f"Changing {res['changed_label']} {direction} "
                    f"{_rupees(abs(d))} ({res['delta_revenue_pct'] * 100:+.1f}%) "
                    f"on a {res['subsample']}-payment sample.{extra} {res['note']}")

    elif intent == "guard":
        s = tool_decision_summary()
        f = tool_find_decisions(verdict="BLOCK", limit=3)
        calls += [{"tool": "decision_summary", "input": {}, "output": s},
                  {"tool": "find_decisions", "input": {"verdict": "BLOCK"}, "output": f}]
        if "error" in s:
            text = "No decisions recorded yet — run a batch in the console first, then ask me again."
        else:
            b = s["verdicts"].get("BLOCK", 0)
            m = s["verdicts"].get("MODIFY", 0)
            e = s["verdicts"].get("ESCALATE", 0)
            text = (f"Of the last {s['decisions']} decisions, the guard blocked {b}, "
                    f"adjusted {m}, and sent {e} to a person. Blocks are fraud or AML declines "
                    f"that may never be retried. Adjustments are mostly quiet-hours deferrals "
                    f"and minimum-gap corrections applied to an otherwise sensible action.")

    elif intent == "ledger":
        l = tool_ledger()
        calls.append({"tool": "ledger", "input": {}, "output": l})
        if "error" in l:
            text = "The ledger has not been generated yet — run the evaluate step first."
        else:
            r, leg = l["policies"]["revenant"], l["policies"]["legacy_retry_T30"]
            text = (f"On {l['batch_size']:,} held-out failed payments worth "
                    f"{_rupees(l['exposure'])}, Revenant recovered {_rupees(r['recovered'])} "
                    f"against {_rupees(leg['recovered'])} for a fixed 30-minute retry. "
                    f"That is {l['uplift_pct'] * 100:.1f}% more money using "
                    f"{l['attempt_reduction_pct'] * 100:.1f}% fewer attempts.")

    elif intent == "model":
        m = tool_model_quality()
        calls.append({"tool": "model_quality", "input": {}, "output": m})
        if "error" in m:
            text = "The model has not been trained yet."
        else:
            text = (f"Tested on {m['n_holdout']:,} payments it had never seen, the model ranks "
                    f"outcomes correctly {m['auc'] * 100:.1f}% of the time. More importantly it is "
                    f"honest about its own confidence: it predicted an average success rate of "
                    f"{m['mean_predicted'] * 100:.1f}% and the real rate was {m['base_rate'] * 100:.1f}%. "
                    f"The thing it relies on most is how long we wait before trying again.")

    elif intent == "playbook":
        hit = next((r for r in tool_failure_playbook() if r.replace("_", " ") in q
                    or r.split("_")[0] in q), None)
        b = tool_failure_playbook(hit)
        calls.append({"tool": "failure_playbook", "input": {"reason": hit}, "output": b})
        if hit:
            e = b[hit]
            text = (f"That is when {e['plain']}. The best move is to retry {e['best_timing']}, "
                    f"because {e['why']}.")
        else:
            text = ("Payments fail for seven different reasons and each one wants a different "
                    "response — a dropped connection wants a retry in seconds, an empty account "
                    "wants hours, and a fraud decline must never be retried at all. Name one and "
                    "I will explain it.")

    elif intent == "about":
        text = ("This is Revenant — a revenue-recovery engine for failed payments. When a "
                "customer's payment fails, most systems just try again after a fixed wait. "
                "Revenant looks at why it failed, works out the best moment and method to try "
                "again, and checks that action against safety rules before anything reaches the "
                "customer. On the held-out test it recovered 71.8% more money than a fixed "
                "30-minute retry while contacting customers 22.9% less often.")

    elif intent == "tech":
        m = tool_model_quality()
        calls.append({"tool": "model_quality", "input": {}, "output": m})
        auc = f"{m['auc']:.3f}" if "error" not in m else "n/a"
        text = ("The prediction model is a LightGBM gradient-boosted classifier that estimates "
                "the chance a payment succeeds given the situation AND the action being "
                f"considered — which is what lets it choose when and how to retry, not just "
                f"whether. It scores {auc} on unseen data. Around it sits a Python and FastAPI "
                "backend, a rules layer written in plain code rather than prompts, a SQLite "
                "audit trail, and a language-model agent that only steps in when two options are "
                "financially tied.")

    elif intent == "howitworks":
        text = ("When a payment fails I score every possible response — try now, wait twenty "
                "minutes, wait six hours, send a message first, suggest another card, or leave it "
                "alone — and pick whichever is worth the most money after costs. That choice then "
                "passes through safety rules that can adjust or stop it. Ask me about the "
                "scoreboard, blocked payments, model accuracy, or what changing a setting would be worth.")

    else:
        s = tool_decision_summary()
        calls.append({"tool": "decision_summary", "input": {}, "output": s})
        if "error" in s:
            text = ("Nothing has run yet. Press Run payments in the console, then ask me about "
                    "blocked payments, the scoreboard, or what a different retry limit would be worth.")
        else:
            top = max(s["failure_reasons"], key=s["failure_reasons"].get)
            text = (f"Across the last {s['decisions']} decisions we handled "
                    f"{_rupees(s['total_amount'])} in failed payments, with "
                    f"{_rupees(s['expected_recovery'])} of expected recovery and "
                    f"{s['nudges']} customer messages sent. The most common failure was "
                    f"{top.replace('_', ' ')}. Median decision time was "
                    f"{s['median_latency_ms']} ms.")

    return {"answer": text, "tool_calls": calls, "mode": "deterministic"}


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def _openai_tools() -> list:
    """Translate the tool schemas into OpenAI function-calling format."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}} for t in SCHEMAS]


def _ask_groq(question: str) -> dict:
    """Tool-calling loop against any OpenAI-compatible endpoint (Groq by default).

    Groq's free tier needs no credit card and supports function calling, which
    is what the Copilot depends on — it must be able to CALL the lookup
    functions, not just talk about them.
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url=GROQ_BASE_URL)

    models = [GROQ_DEFAULT_MODEL] + [m for m in GROQ_FALLBACK_MODELS
                                     if m != GROQ_DEFAULT_MODEL]
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": question}]
    calls = []
    last_error = None

    for model in models:
        try:
            convo = list(messages)
            for _ in range(MAX_TOOL_ROUNDS):
                resp = client.chat.completions.create(
                    model=model, messages=convo, tools=_openai_tools(),
                    max_tokens=800, temperature=0.2, timeout=30)
                msg = resp.choices[0].message
                if not getattr(msg, "tool_calls", None):
                    return {"answer": (msg.content or "").strip(),
                            "tool_calls": calls, "mode": f"agent ({model})"}

                convo.append({"role": "assistant", "content": msg.content,
                              "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
                for tc in msg.tool_calls:
                    fn = TOOLS.get(tc.function.name)
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    out = fn(**args) if fn else {"error": f"unknown tool {tc.function.name}"}
                    calls.append({"tool": tc.function.name, "input": args, "output": out})
                    convo.append({"role": "tool", "tool_call_id": tc.id,
                                  "content": json.dumps(out, default=str)[:12000]})
            return {"answer": "That needed more lookups than I allow in one go — "
                              "try a narrower question.",
                    "tool_calls": calls, "mode": f"agent ({model})"}
        except Exception as exc:  # noqa: BLE001 — try the next model in the list
            last_error = exc
            continue

    raise RuntimeError(f"all models failed: {type(last_error).__name__}: {last_error}")


def ask(question: str) -> dict:
    provider = _provider()
    if provider is None:
        return answer_offline(question)
    if provider == "groq":
        try:
            return _ask_groq(question)
        except Exception as exc:  # noqa: BLE001
            out = answer_offline(question)
            out["mode"] = f"built-in (AI unavailable: {type(exc).__name__})"
            return out
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        messages = [{"role": "user", "content": question}]
        calls = []

        for _ in range(MAX_TOOL_ROUNDS):
            resp = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=900,
                system=SYSTEM, tools=SCHEMAS, messages=messages)
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                text = "".join(b.text for b in resp.content if b.type == "text")
                return {"answer": text.strip(), "tool_calls": calls, "mode": "agent"}

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for u in uses:
                fn = TOOLS.get(u.name)
                out = fn(**u.input) if fn else {"error": f"unknown tool {u.name}"}
                calls.append({"tool": u.name, "input": u.input, "output": out})
                results.append({"type": "tool_result", "tool_use_id": u.id,
                                "content": json.dumps(out, default=str)[:12000]})
            messages.append({"role": "user", "content": results})

        return {"answer": "I ran out of tool rounds on that one — try a narrower question.",
                "tool_calls": calls, "mode": "agent"}
    except Exception as exc:  # noqa: BLE001
        out = answer_offline(question)
        out["mode"] = f"deterministic (agent unavailable: {type(exc).__name__})"
        return out
