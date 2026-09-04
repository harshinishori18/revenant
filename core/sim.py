"""
core/sim.py — Revenant's ground-truth recovery environment.

WHY THIS FILE EXISTS
--------------------
A revenue-recovery engine can only be graded if you can answer a counterfactual:
"what would this SAME failed payment have done under a DIFFERENT action?"

Production logs cannot answer that — you only ever observe the action you took.
So Revenant defines an explicit generative environment. Every failed payment
carries hidden state (when the customer's funds actually arrive, how long the
bank impairment really lasts, how fast the user abandons the checkout session).
An action -> outcome function then scores ANY policy on the SAME batch of
failures, with the same latent draws.

That is what turns "money recovered" from a marketing number into a measured
one, and it is the single design decision the whole project rests on.

The model NEVER sees latent state. It sees only what a PSP sees at the moment
of failure. No leakage, no circular labels.
"""
from __future__ import annotations

import numpy as np

BANKS = ["HDFC", "ICICI", "SBI", "Axis", "Kotak", "YesBank", "IDFC"]
METHODS = ["UPI", "Card", "NetBanking", "Wallet"]
DEVICES = ["android", "ios", "web"]
NETQ = ["good", "moderate", "poor"]
REASONS = [
    "insufficient_funds", "bank_timeout", "wrong_otp",
    "network_drop", "risk_block", "card_declined", "gateway_error",
]

STRATEGIES = ["no_action", "instant_retry", "delayed_retry",
              "nudge_then_retry", "suggest_alt_method"]
CHANNELS = ["none", "sms", "whatsapp", "email"]

# Indian checkout traffic is bimodal: a lunchtime bump and a large evening peak.
HOURLY_W = np.array([1, 1, 1, 1, 1, 2, 3, 5, 7, 8, 9, 9, 8, 7, 7, 8, 9, 10, 10, 9, 7, 5, 3, 2], float)
HOURLY_W /= HOURLY_W.sum()

OBSERVED_FIELDS = ["hour", "amount", "method", "bank", "device",
                   "network_quality", "prior_attempts", "reason"]


def sample_failures(n: int, seed: int = 0) -> dict:
    """Generate n failed payments: observable features + hidden latent state.

    Latent keys are prefixed with '_' and must never reach a model or an API
    response. `strip_latent()` enforces that at the boundary.
    """
    rng = np.random.default_rng(seed)

    method = rng.choice(METHODS, n, p=[0.55, 0.25, 0.12, 0.08])
    bank = rng.choice(BANKS, n)
    device = rng.choice(DEVICES, n, p=[0.55, 0.25, 0.20])
    netq = rng.choice(NETQ, n, p=[0.60, 0.30, 0.10])
    hour = rng.choice(np.arange(24), n, p=HOURLY_W)
    amount = np.round(rng.lognormal(6.6, 1.05, n), 2)
    prior_attempts = rng.poisson(0.55, n)

    # ---- failure reason: driven by CONTEXT, never by the recovery outcome ----
    reason = np.empty(n, dtype=object)
    for i in range(n):
        if netq[i] == "poor" and rng.random() < 0.75:
            reason[i] = rng.choice(
                ["network_drop", "bank_timeout", "gateway_error"], p=[0.55, 0.28, 0.17])
        elif method[i] == "Card":
            reason[i] = rng.choice(
                ["card_declined", "insufficient_funds", "risk_block", "bank_timeout", "gateway_error"],
                p=[0.30, 0.26, 0.18, 0.16, 0.10])
        elif method[i] == "UPI":
            reason[i] = rng.choice(
                ["wrong_otp", "insufficient_funds", "bank_timeout", "network_drop", "risk_block"],
                p=[0.30, 0.30, 0.22, 0.13, 0.05])
        else:
            reason[i] = rng.choice(REASONS, p=[0.22, 0.22, 0.10, 0.10, 0.06, 0.10, 0.20])

    # High-ticket card attempts skew to risk blocks — realistic, and a compliance trap.
    flip = (method == "Card") & (amount > 12000) & (rng.random(n) < 0.35)
    reason[flip] = "risk_block"

    # ---------------- HIDDEN latent state (never exposed) ----------------
    funds_eta = rng.exponential(np.clip(amount / 900.0, 0.4, 40.0))   # hours until balance covers it
    outage_min = rng.lognormal(2.9, 0.85, n)                          # median ~18 min impairment
    patience_min = rng.lognormal(2.4, 0.70, n)                        # median ~11 min session patience
    nudge_affinity = rng.beta(2.4, 2.0, n)                            # will they respond to a nudge

    return dict(
        hour=hour, amount=amount, method=method, bank=bank, device=device,
        network_quality=netq, prior_attempts=prior_attempts, reason=reason,
        _funds_eta=funds_eta, _outage_min=outage_min,
        _patience_min=patience_min, _nudge_affinity=nudge_affinity,
    )


def as_rows(f: dict, prefix: str = "txn") -> list[dict]:
    n = len(f["hour"])
    keys = list(f.keys())
    out = []
    for i in range(n):
        r = {k: (f[k][i].item() if hasattr(f[k][i], "item") else f[k][i]) for k in keys}
        r["txn_id"] = f"{prefix}_{i:06d}"
        out.append(r)
    return out


def strip_latent(row: dict) -> dict:
    """Boundary guard: nothing starting with '_' leaves the simulator."""
    return {k: v for k, v in row.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# The environment's outcome function.
# ---------------------------------------------------------------------------
CHANNEL_REACH = {"none": 0.0, "sms": 0.55, "whatsapp": 0.78, "email": 0.35}


def success_prob(row: dict, strategy: str, delay_min: float, channel: str) -> float:
    """P(payment succeeds) for one failure under one action.

    The important structure here: different failure reasons have OPPOSITE
    optimal delays.

      network_drop / wrong_otp  -> session-bound. Value decays in minutes;
                                   retry NOW, before the customer walks away.
      insufficient_funds        -> wait. Success rises with time as the balance
                                   is topped up. A 3-minute retry is wasted.
      bank_timeout / gateway    -> step function. Useless during the outage,
                                   high the moment it clears.

    A single fixed retry interval — the default build for this track — is
    structurally wrong for at least two of those three at all times. That
    tension is the actual learning problem.
    """
    if strategy == "no_action":
        return 0.0
    if row["reason"] == "risk_block":       # fraud/AML decline: never recoverable
        return 0.0

    r = row["reason"]
    delay_h = delay_min / 60.0
    nudged = channel != "none"
    lift = (row["_nudge_affinity"] * CHANNEL_REACH[channel]) if nudged else 0.0

    if r == "insufficient_funds":
        p = 1.0 - np.exp(-max(delay_h, 0.01) / max(row["_funds_eta"], 0.05))
        p *= 0.55 + 0.45 * lift                      # a nudge prompts the top-up
        if strategy == "suggest_alt_method":
            p = max(p, 0.30 + 0.35 * lift)
    elif r in ("bank_timeout", "gateway_error"):
        p = 0.90 if delay_min > row["_outage_min"] else 0.12
        p *= np.exp(-max(delay_min - row["_outage_min"], 0.0) / 900.0)
    elif r == "network_drop":
        p = 0.88 * np.exp(-delay_min / max(row["_patience_min"], 1.0))
        p = min(0.92, p + 0.45 * lift)
    elif r == "wrong_otp":
        p = 0.80 * np.exp(-delay_min / (2.5 * max(row["_patience_min"], 1.0)))
        p = min(0.93, p + 0.40 * lift)
    elif r == "card_declined":
        p = 0.52 * (0.6 + 0.4 * lift) if strategy == "suggest_alt_method" else 0.14
    else:
        p = 0.25

    p *= 0.82 ** row["prior_attempts"]               # retry fatigue
    if row["hour"] in (0, 1, 2, 3, 4, 5):            # night attempts convert worse
        p *= 0.70
    return float(np.clip(p, 0.0, 0.97))


def rollout(row: dict, action: dict, rng) -> tuple[bool, float]:
    """Sample one realised outcome. Returns (success, revenue_recovered)."""
    p = success_prob(row, action["strategy"], action.get("delay_min", 0),
                     action.get("channel", "none"))
    ok = bool(rng.random() < p)
    return ok, (float(row["amount"]) if ok else 0.0)
