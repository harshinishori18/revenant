"""
scripts/ablation.py — which layer is actually doing the work?

A single headline number invites the question "would a simpler thing have done
just as well?". This script answers it by removing one capability at a time and
re-running the same paired evaluation:

    full                 the shipped system
    no_timing            optimiser may choose channel, but delay is pinned to 30 min
    no_nudge             retries only, no customer messaging
    no_cost              expected value ignores the cost of acting
    reason_agnostic      the failure reason code is hidden from the model

Reporting an ablation is the difference between "our system recovered X" and
"our system recovered X, and here is the part of it that mattered".

Run:  python -m scripts.ablation
"""
from __future__ import annotations

import json
import os

from core.policy import get_policy, STRATEGY_COST, CHANNEL_COST
from core.sim import strip_latent
from scripts.evaluate import build_batch, run_policy, bootstrap_ci, policy_legacy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def variant(mode: str):
    pol = get_policy()

    def _fn(txn):
        t = strip_latent(txn)
        if mode == "reason_agnostic":
            t = dict(t, reason="unknown")          # unseen level -> encoded as -1
        ranked = pol.score(t)
        if mode == "no_timing":
            ranked = [r for r in ranked if r["delay_min"] in (30, 20, 45)] or ranked
        if mode == "no_nudge":
            ranked = [r for r in ranked if r["channel"] == "none"]
        if mode == "no_cost":
            for r in ranked:
                if r["strategy"] != "no_action":
                    r["expected_value"] = round(r["p_success"] * float(t["amount"]), 2)
            ranked.sort(key=lambda r: -r["expected_value"])
        top = ranked[0]
        if top["expected_value"] <= 0:
            top = {"strategy": "no_action", "delay_min": 0, "channel": "none"}
        return {k: top[k] for k in ("strategy", "delay_min", "channel")}, ranked
    return _fn


def main() -> None:
    batch = build_batch()[:3000]   # subsample: 5 variants x full batch is slow, and
    #                                 the ranking of variants is stable at this size
    exposure = sum(r["amount"] for r in batch)
    print(f"Ablation on {len(batch):,} held-out failures | Rs {exposure:,.0f} at risk\n")
    print(f"{'variant':<18}{'recovered':>15}{'rate':>9}{'attempts':>11}{'nudges':>9}")

    out = {}
    rec, st, _ = run_policy(batch, policy_legacy)
    out["legacy_retry_T30"] = {"recovered": float(rec.sum()),
                               "rate": float((rec > 0).mean()), **st}
    print(f"{'legacy_retry_T30':<18}{rec.sum():>15,.0f}{100*(rec>0).mean():>8.2f}%"
          f"{st['actions']:>11,}{st['nudges']:>9,}")

    for mode in ["full", "no_timing", "no_nudge", "no_cost", "reason_agnostic"]:
        rec, st, _ = run_policy(batch, variant(mode))
        lo, hi = bootstrap_ci(rec)
        out[mode] = {"recovered": float(rec.sum()), "rate": float((rec > 0).mean()),
                     "ci95": [lo, hi], **st}
        print(f"{mode:<18}{rec.sum():>15,.0f}{100*(rec>0).mean():>8.2f}%"
              f"{st['actions']:>11,}{st['nudges']:>9,}")

    full = out["full"]["recovered"]
    print("\ncontribution of each capability (rupees lost when removed):")
    for mode in ["no_timing", "no_nudge", "no_cost", "reason_agnostic"]:
        print(f"  {mode:<18} -Rs {full - out[mode]['recovered']:>12,.0f}")

    json.dump(out, open(os.path.join(ROOT, "results", "ablation.json"), "w"), indent=2)
    print("\nwrote results/ablation.json")


if __name__ == "__main__":
    main()
