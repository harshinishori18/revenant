"""
core/whatif.py — the policy sandbox.

A merchant's real question is never "what did the model decide?". It is
"what happens to my money if I loosen the retry cap?" or "what is the quiet-hours
rule costing me?".

This re-runs the counterfactual simulation with overridden guard parameters and
reports the measured delta against the shipped configuration.

PERFORMANCE NOTE (this is why the sandbox is interactive)
---------------------------------------------------------
The naive implementation walks payments one at a time, calling the model once
per payment per attempt — roughly 2,200 model calls per scenario. That took
~8 seconds and blocked the API worker while it ran, which also stalled any
Copilot question asked in the meantime.

Every payment within an attempt round is independent, so this version is
ROUND-BASED: collect all payments still in flight, score them in a single
batched model call, apply the guard, sample outcomes, carry survivors into the
next round. Four model calls per scenario instead of thousands. Identical
arithmetic, roughly two orders of magnitude faster.

COMPLIANCE NOTE (worth saying in the demo)
------------------------------------------
RISK_BLOCK_NO_RETRY is deliberately NOT tunable. Some rules are business
preferences; some are not negotiable, and a policy sandbox has to know the
difference or it is just a settings page.
"""
from __future__ import annotations

import threading

import numpy as np

from core import guard
from core.policy import get_policy
from core.sim import sample_failures, as_rows, success_prob, strip_latent

SUBSAMPLE = 600
SEED = 77
RNG_STREAM = 2_024

TUNABLE = {
    "MAX_ATTEMPTS": (1, 6),
    "MIN_SPACING_MIN": (0, 240),
    "DND_START": (0, 23),
    "DND_END": (0, 23),
    "HUMAN_REVIEW_AMOUNT": (500.0, 100_000.0),
}

LABELS = {
    "MAX_ATTEMPTS": ("Retry limit",
                     "How many times we may re-try one payment before stopping."),
    "MIN_SPACING_MIN": ("Minimum gap",
                        "The shortest wait allowed between two attempts on the same payment."),
    "DND_START": ("Quiet hours start",
                  "After this hour we stop messaging customers."),
    "DND_END": ("Quiet hours end",
                "Before this hour we stop messaging customers."),
    "HUMAN_REVIEW_AMOUNT": ("Manual review above",
                            "Payments this large go to a person instead of being handled automatically."),
}

_batch_cache: list[dict] | None = None
_baseline: dict | None = None
_lock = threading.Lock()
_ready = threading.Event()


def _batch() -> list[dict]:
    global _batch_cache
    if _batch_cache is None:
        _batch_cache = as_rows(sample_failures(8_000, seed=SEED), prefix="eval")[:SUBSAMPLE]
    return _batch_cache


def _run(overrides: dict | None) -> dict:
    """Round-based episode simulation with guard constants temporarily patched."""
    original = {k: getattr(guard, k) for k in TUNABLE}
    try:
        for k, v in (overrides or {}).items():
            if k in TUNABLE:
                lo, hi = TUNABLE[k]
                setattr(guard, k, type(original[k])(min(max(v, lo), hi)))

        pol = get_policy()
        rng = np.random.default_rng(RNG_STREAM)
        recovered = 0.0
        stats = dict(actions=0, nudges=0, blocked=0, escalated=0, declined=0)

        live = [dict(r) for r in _batch()]

        for _round in range(int(guard.MAX_ATTEMPTS) + 1):
            if not live:
                break
            proposals = pol.decide_batch([strip_latent(t) for t in live])
            survivors = []
            for txn, proposed in zip(live, proposals):
                final = guard.validate(strip_latent(txn), dict(proposed))
                verdict = final["guard"]["verdict"]

                if verdict == "BLOCK":
                    stats["blocked"] += 1
                    continue
                if verdict == "ESCALATE":
                    stats["escalated"] += 1
                    continue
                if final["strategy"] == "no_action":
                    stats["declined"] += 1
                    continue

                stats["actions"] += 1
                if final["channel"] != "none":
                    stats["nudges"] += 1

                p = success_prob(txn, final["strategy"], final["delay_min"], final["channel"])
                if rng.random() < p:
                    recovered += float(txn["amount"])
                    continue

                txn["prior_attempts"] += 1
                txn["hour"] = int((txn["hour"] + final["delay_min"] // 60) % 24)
                survivors.append(txn)
            live = survivors

        return {"recovered": round(recovered, 2), **stats}
    finally:
        for k, v in original.items():
            setattr(guard, k, v)


def baseline() -> dict:
    """The shipped configuration's result. Computed once, then reused."""
    global _baseline
    with _lock:
        if _baseline is None:
            _baseline = _run(None)
            _ready.set()
        return _baseline


def warm() -> None:
    """Called at server startup, off the request path, so the first click is fast."""
    try:
        baseline()
    except Exception:  # noqa: BLE001 — warming is best-effort
        pass


def is_ready() -> bool:
    return _ready.is_set()


def simulate(overrides: dict) -> dict:
    base = baseline()
    scen = _run(overrides)
    d_rev = scen["recovered"] - base["recovered"]
    d_act = scen["actions"] - base["actions"]

    changed = [f"{LABELS[k][0]} \u2192 {v:g}" for k, v in overrides.items() if k in LABELS]

    return {
        "subsample": SUBSAMPLE,
        "overrides": overrides,
        "changed_label": ", ".join(changed) if changed else "no change",
        "baseline": base,
        "scenario": scen,
        "delta_revenue": round(d_rev, 2),
        "delta_revenue_pct": round(d_rev / max(base["recovered"], 1), 4),
        "delta_actions": d_act,
        "delta_nudges": scen["nudges"] - base["nudges"],
        "delta_escalated": scen["escalated"] - base["escalated"],
        "revenue_per_extra_attempt": (round(d_rev / d_act, 2) if d_act > 0 else None),
        "note": ("Risk and fraud blocks stay switched on in every scenario — "
                 "that rule is not adjustable."),
    }


def defaults() -> dict:
    return {k: getattr(guard, k) for k in TUNABLE}


def schema() -> dict:
    return {
        "defaults": defaults(),
        "tunable": {k: {"range": list(v), "label": LABELS[k][0], "help": LABELS[k][1]}
                    for k, v in TUNABLE.items()},
        "locked": [{"code": "RISK_BLOCK_NO_RETRY",
                    "text": "Payments declined for fraud or AML reasons are never retried."}],
        "subsample": SUBSAMPLE,
    }
