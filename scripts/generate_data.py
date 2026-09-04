"""
scripts/generate_data.py — build the LOGGED history Revenant trains on.

Methodological point worth saying out loud in the pitch:

We log what a *randomised legacy retry policy* did — the same shape of data a
real PSP already has sitting in its warehouse. Because that logging policy
varies its actions, the dataset contains genuine action variation, which is
exactly what makes learning an action-conditioned model (and evaluating a new
policy off-policy) valid rather than circular.

The model sees only observable fields. It never sees latent state, and it never
sees a label derived from a lookup on its own inputs.

Run:  python -m scripts.generate_data
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

from core.sim import sample_failures, as_rows, rollout, OBSERVED_FIELDS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

N_TRAIN = 60_000
N_EVAL = 8_000


def legacy_logging_action(rng) -> dict:
    """A messy, partially randomised retry policy — i.e. what merchants do today."""
    s = rng.choice(["instant_retry", "delayed_retry", "nudge_then_retry",
                    "suggest_alt_method", "no_action"],
                   p=[0.28, 0.30, 0.24, 0.10, 0.08])
    if s == "no_action":
        return {"strategy": s, "delay_min": 0, "channel": "none"}
    if s == "instant_retry":
        delay = float(rng.uniform(0, 8))
    elif s == "delayed_retry":
        delay = float(np.exp(rng.uniform(np.log(10), np.log(2880))))
    else:
        delay = float(np.exp(rng.uniform(np.log(5), np.log(1440))))
    channel = ("none" if s in ("instant_retry", "delayed_retry")
               else str(rng.choice(["sms", "whatsapp", "email"], p=[0.40, 0.40, 0.20])))
    return {"strategy": s, "delay_min": round(delay, 1), "channel": channel}


def build(n: int, seed: int, prefix: str) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 9_999)
    rows = as_rows(sample_failures(n, seed=seed), prefix=prefix)
    records = []
    for row in rows:
        action = legacy_logging_action(rng)
        success, revenue = rollout(row, action, rng)
        rec = {"txn_id": row["txn_id"], **{k: row[k] for k in OBSERVED_FIELDS}}
        rec.update(strategy=action["strategy"], delay_min=action["delay_min"],
                   channel=action["channel"], success=int(success),
                   revenue_recovered=revenue)
        records.append(rec)
    return pd.DataFrame(records)


def main() -> None:
    os.makedirs(DATA, exist_ok=True)
    train = build(N_TRAIN, seed=1, prefix="train")
    evalb = build(N_EVAL, seed=77, prefix="eval")
    train.to_csv(os.path.join(DATA, "train_log.csv"), index=False)
    evalb.to_csv(os.path.join(DATA, "eval_batch.csv"), index=False)

    print(f"train_log.csv  {train.shape[0]:,} rows")
    print(f"eval_batch.csv {evalb.shape[0]:,} rows  (held out — never trained on)\n")
    print("failure mix:")
    print(train["reason"].value_counts(normalize=True).mul(100).round(1).to_string())
    print(f"\nlegacy logging policy recovery rate: {100 * train.success.mean():.1f}%")
    print(f"exposure in eval batch: Rs {evalb.amount.sum():,.0f}")


if __name__ == "__main__":
    main()
