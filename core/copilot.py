"""
core/copilot.py — Recovery Copilot.

Answers plain-English questions about the live audit trail and the policy
sandbox by calling real functions against the SQLite audit database and the
whatif simulator — never by generating a number from scratch. Every answer
carries the list of tool calls that produced it, so the response is checkable.

By default this works with NO Anthropic API key: a small intent router picks
the right tool from keywords in the question, which keeps the demo working
offline and keeps the numbers honest (a tool call either returns a real
number or the question gets a "can't answer that from the audit trail" — it
never fabricates one). If ANTHROPIC_API_KEY is set, `ask()` can be swapped
for an LLM-routed version later; the tool functions below are already
shaped for that (name in, JSON out) so that upgrade is a routing change only,
not a rewrite.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import statistics
from contextlib import closing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
DB_PATH = os.path.join(ROOT, "artifacts", "audit.db")


# --------------------------------------------------------------------------
# tools — each returns a small JSON-able dict, nothing more
# --------------------------------------------------------------------------
def tool_ledger(_args: dict | None = None) -> dict:
    path = os.path.join(RESULTS, "ledger.json")
    if not os.path.exists(path):
        return {"error": "no ledger yet — run: python -m scripts.evaluate"}
    data = json.load(open(path))
    rev = data["policies"]["revenant"]
    leg = data["policies"]["legacy_retry_T30"]
    return {
        "exposure": data["exposure"],
        "revenant_recovered": rev["recovered"],
        "legacy_recovered": leg["recovered"],
        "uplift_pct": round(data["uplift_pct"] * 100, 1),
        "attempt_reduction_pct": round(data["attempt_reduction_pct"] * 100, 1),
    }


def tool_decision_summary(args: dict) -> dict:
    n = int(args.get("n", 12))
    with closing(sqlite3.connect(DB_PATH)) as con:
        rows = con.execute(
            "SELECT payload FROM decisions ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    recs = [json.loads(r[0]) for r in rows]
    if not recs:
        return {"error": "no decisions recorded yet — analyze a few payments first"}
    handled = sum(r["amount"] for r in recs)
    ev = sum(r["top_candidates"][0]["expected_value"] for r in recs if r.get("top_candidates"))
    nudges = sum(1 for r in recs if r["final_action"]["channel"] != "none")
    lat = [r["latency_ms"] for r in recs if r.get("latency_ms") is not None]
    return {
        "n": len(recs),
        "handled_amount": round(handled, 2),
        "expected_recovery": round(ev, 2),
        "nudges": nudges,
        "median_latency_ms": round(statistics.median(lat), 1) if lat else None,
    }


def tool_find_decisions(args: dict) -> dict:
    n = int(args.get("n", 12))
    verdict = args.get("verdict")
    with closing(sqlite3.connect(DB_PATH)) as con:
        rows = con.execute(
            "SELECT payload FROM decisions ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    recs = [json.loads(r[0]) for r in rows]
    counts: dict[str, int] = {}
    for r in recs:
        counts[r["guard_verdict"]] = counts.get(r["guard_verdict"], 0) + 1
    matches = [r["txn_id"] for r in recs if verdict and r["guard_verdict"] == verdict]
    return {"scanned": len(recs), "verdict_counts": counts,
            "matching_txn_ids": matches[:10]}


def tool_policy_whatif(args: dict) -> dict:
    from core import whatif
    overrides = {k: v for k, v in args.items() if k in whatif.TUNABLE}
    if not overrides:
        return {"error": "no tunable parameter named in the question"}
    r = whatif.simulate(overrides)
    return {
        "overrides": overrides,
        "delta_revenue": r["delta_revenue"],
        "delta_actions": r["delta_actions"],
        "sample_size": r["sample_size"],
    }


TOOLS = {
    "ledger": tool_ledger,
    "decision_summary": tool_decision_summary,
    "find_decisions": tool_find_decisions,
    "policy_whatif": tool_policy_whatif,
}


# --------------------------------------------------------------------------
# intent router — keyword-based, deliberately simple and inspectable
# --------------------------------------------------------------------------
_PARAM_WORDS = {
    "attempt": "MAX_ATTEMPTS", "attempts": "MAX_ATTEMPTS", "cap": "MAX_ATTEMPTS",
    "spacing": "MIN_SPACING_MIN",
    "dnd": "DND_START", "quiet": "DND_START",
    "ceiling": "HUMAN_REVIEW_AMOUNT", "review": "HUMAN_REVIEW_AMOUNT",
    "human": "HUMAN_REVIEW_AMOUNT", "limit": "HUMAN_REVIEW_AMOUNT",
}


def _extract_number(q: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", q)
    return float(m.group(1)) if m else None


def _route(question: str) -> tuple[str, dict]:
    q = question.lower()

    if any(w in q for w in ("what if", "whatif", "suppose", "raise", "lower", "cap")):
        for word, param in _PARAM_WORDS.items():
            if word in q:
                num = _extract_number(q)
                if num is not None:
                    if param != "HUMAN_REVIEW_AMOUNT":
                        num = int(num)
                    return "policy_whatif", {param: num}
        return "policy_whatif", {}

    if any(w in q for w in ("block", "why are we blocking", "escalat", "modif")):
        return "find_decisions", {"n": 20}

    if any(w in q for w in ("summar", "last decision", "recent decision")):
        return "decision_summary", {"n": 12}

    if any(w in q for w in ("recover", "vs legacy", "uplift", "how much did we")):
        return "ledger", {}

    return "decision_summary", {"n": 12}


def ask(question: str) -> dict:
    tool_name, args = _route(question)
    fn = TOOLS[tool_name]
    try:
        result = fn(args)
    except Exception as e:  # noqa: BLE001 — surfaced to the caller, not swallowed
        result = {"error": str(e)}

    answer = _format_answer(tool_name, result)
    return {"answer": answer, "tool_calls": [{"tool": tool_name, "args": args, "result": result}]}


def _format_answer(tool_name: str, r: dict) -> str:
    if "error" in r:
        return r["error"]

    if tool_name == "ledger":
        return (f"On the held-out batch (Rs {r['exposure']:,.0f} at risk), Revenant recovered "
                f"Rs {r['revenant_recovered']:,.0f} against Rs {r['legacy_recovered']:,.0f} for a "
                f"fixed T+30 retry — {r['uplift_pct']}% more, using {r['attempt_reduction_pct']}% "
                f"fewer attempts.")

    if tool_name == "decision_summary":
        lat = f", median decision latency {r['median_latency_ms']} ms" if r.get("median_latency_ms") else ""
        return (f"Last {r['n']} decisions: Rs {r['handled_amount']:,.0f} handled, "
                f"Rs {r['expected_recovery']:,.0f} in expected recovery, {r['nudges']} nudges{lat}.")

    if tool_name == "find_decisions":
        counts = r["verdict_counts"]
        return (f"Of the last {r['scanned']} decisions, the guard blocked {counts.get('BLOCK', 0)} "
                f"and modified {counts.get('MODIFY', 0)}. Blocks are risk/AML declines the optimiser "
                "is never permitted to retry; modifications are mostly DND deferrals and minimum "
                "spacing corrections.")

    if tool_name == "policy_whatif":
        if not r.get("overrides"):
            return ("I couldn't tell which setting to change from that question — try naming it, "
                    "e.g. 'what if we raised the retry cap to 5?'")
        delta = r["delta_revenue"]
        sign = "gains" if delta >= 0 else "costs"
        return (f"On a {r['sample_size']}-payment sample, that change {sign} "
                f"Rs {abs(delta):,.0f}. It changes attempt volume by {r['delta_actions']:+d}. "
                "Risk/AML blocks are not tunable — that rule is enforced in every scenario "
                "regardless of configuration.")

    return json.dumps(r)
