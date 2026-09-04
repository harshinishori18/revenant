"""
core/whatif.py — Revenant's policy sandbox.

Answers "what would changing a compliance setting do to the money?" by
re-running the SAME counterfactual episode runner scripts/evaluate.py uses,
on the same held-out batch, with one or more guard constants overridden for
the duration of the run. RISK_BLOCK_NO_RETRY is never touched — that rule is
enforced unconditionally inside guard.validate() and this module does not
attempt to bypass it.

This is a measurement, not an estimate: every number returned here comes from
actually replaying the batch under the new setting, the same way
scripts/evaluate.py produces the numbers in results/ledger.json.
"""
from __future__ import annotations

from contextlib import contextmanager

from core import guard
from core.policy import get_policy
from core.sim import sample_failures, as_rows, success_prob, strip_latent

N = 900          # smaller than the full 8,000-row eval batch: a sandbox
SEED = 77        # scenario should return in well under a second
RNG_STREAM = 2024

TUNABLE = {
    "MAX_ATTEMPTS": (1, 6),
    "MIN_SPACING_MIN": (0, 60),
    "DND_START": (18, 23),
    "DND_END": (5, 11),
    "HUMAN_REVIEW_AMOUNT": (2000.0, 50000.0),
}


def defaults() -> dict:
    return {k: getattr(guard, k) for k in TUNABLE}


@contextmanager
def _overrides(cfg: dict):
    saved = {k: getattr(guard, k) for k in cfg}
    try:
        for k, v in cfg.items():
            setattr(guard, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(guard, k, v)


def _batch() -> list[dict]:
    return as_rows(sample_failures(N, seed=SEED), prefix="wif")


def _run(batch: list[dict]) -> dict:
    import numpy as np
    pol = get_policy()
    rng = np.random.default_rng(RNG_STREAM)
    recovered = np.zeros(len(batch))
    stats = dict(actions=0, nudges=0, blocked=0, escalated=0, declined=0)

    for i, base in enumerate(batch):
        txn = dict(base)
        for _ in range(guard.MAX_ATTEMPTS + 1):
            top, _ranked = pol.decide(strip_latent(txn))
            proposed = {k: top[k] for k in ("strategy", "delay_min", "channel")}
            final = guard.validate(strip_latent(txn), proposed)
            verdict = final["guard"]["verdict"]

            if verdict == "BLOCK":
                stats["blocked"] += 1
                break
            if verdict == "ESCALATE":
                stats["escalated"] += 1
                break
            if final["strategy"] == "no_action":
                stats["declined"] += 1
                break

            stats["actions"] += 1
            if final["channel"] != "none":
                stats["nudges"] += 1

            p = success_prob(txn, final["strategy"], final["delay_min"], final["channel"])
            if rng.random() < p:
                recovered[i] = float(txn["amount"])
                break
            txn = dict(txn,
                       prior_attempts=txn["prior_attempts"] + 1,
                       hour=int((txn["hour"] + final["delay_min"] // 60) % 24))
    return {"recovered": float(recovered.sum()), **stats}


def baseline() -> dict:
    return _run(_batch())


def simulate(overrides: dict) -> dict:
    """Re-run the sandbox batch under `overrides`, compared against the
    shipped configuration on the SAME batch (same seed, same RNG stream —
    paired, so the delta is attributable to the setting, not to sampling
    noise)."""
    bad = set(overrides) - set(TUNABLE)
    if bad:
        raise ValueError(f"not tunable: {sorted(bad)}")

    batch = _batch()
    base = _run(batch)
    with _overrides(overrides):
        scen = _run(batch)

    return {
        "overrides": overrides,
        "baseline": base,
        "scenario": scen,
        "delta_revenue": round(scen["recovered"] - base["recovered"], 2),
        "delta_actions": scen["actions"] - base["actions"],
        "delta_nudges": scen["nudges"] - base["nudges"],
        "delta_blocked": scen["blocked"] - base["blocked"],
        "delta_escalated": scen["escalated"] - base["escalated"],
        "sample_size": N,
    }
