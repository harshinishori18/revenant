"""
core/guard.py — Revenant's compliance guard.

The track bar asks for compliant stopping rules, escalation paths and an audit
trail. None of those can live inside an LLM prompt: a prompt is a request, not
a control. Every proposed action — whether it came from the policy optimiser
or from the LLM agent — passes through `validate()` before it can execute.

The guard can PASS, MODIFY, BLOCK or ESCALATE, and always returns a
machine-readable code that is written to the audit log. It is the last word.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

MODEL_VERSION = "revenant-lgbm-1.0"

# ---- policy constants (a real deployment would load these per-merchant) ----
MAX_ATTEMPTS = 3            # total re-attempts permitted per payment
MIN_SPACING_MIN = 15        # minimum gap between consecutive attempts
DND_START, DND_END = 21, 9  # no customer-facing nudges 21:00–09:00 IST (TRAI)
MAX_DELAY_MIN = 4320        # 72h authorisation window ceiling
NEVER_RETRY = {"risk_block"}          # fraud / AML declines
HUMAN_REVIEW_AMOUNT = 10000.0         # above this, a human decides

RULEBOOK = [
    ("RISK_BLOCK_NO_RETRY", f"Never re-attempt a payment declined for risk/AML reasons."),
    ("MAX_ATTEMPTS", f"Stop after {MAX_ATTEMPTS} recovery attempts on one payment."),
    ("HIGH_VALUE", f"Escalate payments at or above Rs {HUMAN_REVIEW_AMOUNT:,.0f} to a human reviewer."),
    ("MIN_SPACING", f"Leave at least {MIN_SPACING_MIN} minutes between attempts."),
    ("DND_HOURS", f"Send no customer nudge between {DND_START}:00 and {DND_END}:00 IST."),
    ("AUTH_WINDOW", f"Never schedule an attempt more than {MAX_DELAY_MIN // 60} hours out."),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _block(action: dict, code: str, msg: str) -> dict:
    return {"strategy": "no_action", "delay_min": 0, "channel": "none",
            "rationale": msg,
            "guard": {"verdict": "BLOCK", "code": code, "notes": [msg],
                      "original": action}}


def validate(txn: dict, action: dict) -> dict:
    """Return the FINAL executable action, annotated with the guard verdict."""
    a = dict(action)
    a.setdefault("delay_min", 0)
    a.setdefault("channel", "none")
    a["delay_min"] = float(a["delay_min"])
    notes: list[str] = []

    reason = txn.get("reason")
    attempts = int(txn.get("prior_attempts", 0))
    amount = float(txn.get("amount", 0.0))
    hour = int(txn.get("hour", 12))

    if a["strategy"] == "no_action":
        a["guard"] = {"verdict": "PASS", "code": "NOOP", "notes": [], "original": action}
        return a

    # ---------------- hard stops ----------------
    if reason in NEVER_RETRY:
        return _block(action, "RISK_BLOCK_NO_RETRY",
                      "Blocked: risk/AML decline — re-attempting this payment is not permitted.")
    if attempts >= MAX_ATTEMPTS:
        return _block(action, "MAX_ATTEMPTS",
                      f"Blocked: retry cap of {MAX_ATTEMPTS} attempts already reached on this payment.")

    # ---------------- escalation instead of automation ----------------
    if amount >= HUMAN_REVIEW_AMOUNT:
        msg = (f"Escalated: Rs {amount:,.0f} is at or above the Rs {HUMAN_REVIEW_AMOUNT:,.0f} "
               "auto-action ceiling — routed to merchant ops for manual review.")
        return {"strategy": "escalate_human", "delay_min": 0, "channel": "none",
                "rationale": msg,
                "guard": {"verdict": "ESCALATE", "code": "HIGH_VALUE",
                          "notes": [msg], "original": action}}

    # ---------------- downgrades: the action still runs, but narrower ----------------
    if a["delay_min"] < MIN_SPACING_MIN and attempts >= 1:
        a["delay_min"] = float(MIN_SPACING_MIN)
        notes.append(f"Delay raised to {MIN_SPACING_MIN} min — minimum spacing between attempts.")
    if a["delay_min"] > MAX_DELAY_MIN:
        a["delay_min"] = float(MAX_DELAY_MIN)
        notes.append("Delay capped at 72 h — authorisation window limit.")

    exec_hour = int((hour + a["delay_min"] // 60) % 24)
    if a["channel"] != "none" and (exec_hour >= DND_START or exec_hour < DND_END):
        shift = (DND_END - exec_hour) % 24
        a["delay_min"] += shift * 60
        notes.append(f"Nudge deferred {shift} h to 09:00 — TRAI DND quiet hours.")

    a["guard"] = {"verdict": "MODIFY" if notes else "PASS", "code": "OK",
                  "notes": notes, "original": action}
    return a


def feature_hash(txn: dict) -> str:
    """Deterministic fingerprint of the exact inputs a decision was made on."""
    keys = ["hour", "amount", "method", "bank", "device", "network_quality",
            "prior_attempts", "reason"]
    blob = json.dumps({k: txn.get(k) for k in keys}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def audit_record(txn: dict, ranked: list, proposed: dict, final: dict,
                 source: str, latency_ms: float | None = None) -> dict:
    """The immutable decision record. One row per decision, never overwritten."""
    return {
        "ts": _now(),
        "txn_id": txn.get("txn_id"),
        "amount": float(txn.get("amount", 0)),
        "reason": txn.get("reason"),
        "method": txn.get("method"),
        "bank": txn.get("bank"),
        "hour": txn.get("hour"),
        "prior_attempts": txn.get("prior_attempts", 0),
        "model_version": MODEL_VERSION,
        "feature_hash": feature_hash(txn),
        "decision_source": source,
        "candidates_evaluated": len(ranked),
        "top_candidates": ranked[:5],
        "proposed_action": {k: proposed.get(k) for k in ("strategy", "delay_min", "channel")},
        "guard_verdict": final["guard"]["verdict"],
        "guard_code": final["guard"].get("code"),
        "guard_notes": final["guard"].get("notes", []),
        "final_action": {k: final.get(k) for k in ("strategy", "delay_min", "channel")},
        "rationale": final.get("rationale") or proposed.get("rationale"),
        "latency_ms": latency_ms,
    }
