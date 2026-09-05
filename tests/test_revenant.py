"""
tests/test_revenant.py — the properties this system must never violate.

Run:  pytest -q

These are not coverage theatre. Each test pins an invariant that, if it broke
silently, would invalidate the headline result or breach a compliance rule:

  * the guard's hard stops cannot be bypassed, including via the sandbox
  * no latent (unobservable) state can leak into the model's inputs
  * the environment's opposite-delay structure actually holds
  * the policy respects the economics it claims to optimise
  * evaluation is deterministic, so the reported number is reproducible
"""
from __future__ import annotations

import numpy as np
import pytest

from core import guard, whatif
from core.policy import get_policy, CANDIDATES, STRATEGY_COST, CHANNEL_COST
from core.sim import (sample_failures, as_rows, strip_latent, success_prob,
                      OBSERVED_FIELDS, REASONS)


# --------------------------------------------------------------------------
# compliance guard — the rules that must hold regardless of what the model wants
# --------------------------------------------------------------------------
def _txn(**kw):
    base = dict(txn_id="t1", hour=12, amount=1000.0, method="UPI", bank="HDFC",
                device="android", network_quality="good", prior_attempts=0,
                reason="bank_timeout")
    base.update(kw)
    return base


def test_risk_block_is_never_retried():
    """The single most important rule in the system."""
    for strategy in ("instant_retry", "delayed_retry", "nudge_then_retry",
                     "suggest_alt_method"):
        out = guard.validate(_txn(reason="risk_block"),
                             {"strategy": strategy, "delay_min": 30, "channel": "sms"})
        assert out["guard"]["verdict"] == "BLOCK"
        assert out["guard"]["code"] == "RISK_BLOCK_NO_RETRY"
        assert out["strategy"] == "no_action"


def test_attempt_cap_is_enforced():
    out = guard.validate(_txn(prior_attempts=guard.MAX_ATTEMPTS),
                         {"strategy": "delayed_retry", "delay_min": 30, "channel": "none"})
    assert out["guard"]["verdict"] == "BLOCK"
    assert out["guard"]["code"] == "MAX_ATTEMPTS"


def test_high_value_escalates_rather_than_acting():
    out = guard.validate(_txn(amount=guard.HUMAN_REVIEW_AMOUNT + 1),
                         {"strategy": "instant_retry", "delay_min": 0, "channel": "none"})
    assert out["guard"]["verdict"] == "ESCALATE"
    assert out["strategy"] == "escalate_human"


def test_no_customer_contact_during_quiet_hours():
    """A nudge scheduled into the DND window must be deferred, never sent."""
    for hour in (22, 23, 0, 3, 6):
        out = guard.validate(_txn(hour=hour),
                             {"strategy": "nudge_then_retry", "delay_min": 0, "channel": "sms"})
        if out["guard"]["verdict"] == "BLOCK":
            continue
        exec_hour = (hour + int(out["delay_min"] // 60)) % 24
        assert not (exec_hour >= guard.DND_START or exec_hour < guard.DND_END), (
            f"nudge would execute at {exec_hour}:00, inside quiet hours")


def test_minimum_spacing_applied_after_first_attempt():
    out = guard.validate(_txn(prior_attempts=1),
                         {"strategy": "instant_retry", "delay_min": 0, "channel": "none"})
    assert out["delay_min"] >= guard.MIN_SPACING_MIN
    assert out["guard"]["verdict"] == "MODIFY"


def test_delay_never_exceeds_authorisation_window():
    out = guard.validate(_txn(), {"strategy": "delayed_retry",
                                  "delay_min": 99_999, "channel": "none"})
    assert out["delay_min"] <= guard.MAX_DELAY_MIN


def test_guard_leaves_no_decision_unrecorded():
    rec = guard.audit_record(_txn(), [], {"strategy": "instant_retry", "delay_min": 0,
                                          "channel": "none"},
                             guard.validate(_txn(), {"strategy": "instant_retry",
                                                     "delay_min": 0, "channel": "none"}),
                             "policy_optimiser", 4.2)
    for field in ("ts", "txn_id", "guard_verdict", "final_action", "feature_hash",
                  "model_version"):
        assert rec[field] is not None


# --------------------------------------------------------------------------
# leakage — the model must never see the environment's hidden state
# --------------------------------------------------------------------------
def test_latent_state_is_stripped_at_the_boundary():
    row = as_rows(sample_failures(5, seed=3))[0]
    assert any(k.startswith("_") for k in row), "fixture should contain latent keys"
    clean = strip_latent(row)
    assert not any(k.startswith("_") for k in clean)


def test_model_inputs_contain_only_observable_fields():
    pol = get_policy()
    allowed = set(OBSERVED_FIELDS) | {"strategy", "channel", "log_delay"}
    assert set(pol.cols) <= allowed, f"unexpected model inputs: {set(pol.cols) - allowed}"


def test_scoring_rejects_latent_state_silently_rather_than_using_it():
    """Passing latent keys must not change the score — proof they are ignored."""
    pol = get_policy()
    row = as_rows(sample_failures(1, seed=11))[0]
    clean = pol.score(strip_latent(row))[0]
    dirty = pol.score(row)[0]
    assert clean["p_success"] == dirty["p_success"]


# --------------------------------------------------------------------------
# environment — the thesis must actually be present in the data
# --------------------------------------------------------------------------
def test_opposite_optimal_delays_exist():
    """A dropped session wants minutes; an empty account wants hours.

    This is the product's entire premise. If it stops holding, the result is
    meaningless and everything downstream should be treated as suspect.
    """
    rows = as_rows(sample_failures(400, seed=5))
    grid = [1, 5, 30, 120, 480, 1440]

    def best_delay(reason):
        subset = [r for r in rows if r["reason"] == reason][:120]
        assert subset, f"no {reason} rows in fixture"
        curve = [np.mean([success_prob(r, "delayed_retry", d, "none") for r in subset])
                 for d in grid]
        return grid[int(np.argmax(curve))]

    assert best_delay("network_drop") <= 5
    assert best_delay("insufficient_funds") >= 120


def test_risk_block_is_unrecoverable_in_the_environment_too():
    rows = [r for r in as_rows(sample_failures(300, seed=7)) if r["reason"] == "risk_block"]
    assert rows
    for r in rows[:40]:
        for d in (0, 60, 1440):
            assert success_prob(r, "delayed_retry", d, "none") == 0.0


def test_every_failure_reason_is_generated():
    rows = as_rows(sample_failures(3000, seed=9))
    assert set(r["reason"] for r in rows) == set(REASONS)


# --------------------------------------------------------------------------
# policy economics
# --------------------------------------------------------------------------
def test_expected_value_matches_its_definition():
    pol = get_policy()
    txn = strip_latent(as_rows(sample_failures(1, seed=13))[0])
    for row in pol.score(txn):
        if row["strategy"] == "no_action":
            assert row["expected_value"] == 0.0
            continue
        cost = STRATEGY_COST[row["strategy"]] + CHANNEL_COST[row["channel"]]
        expected = row["p_success"] * float(txn["amount"]) - cost
        # p_success is stored rounded to 4dp, so the tolerance scales with amount
        tol = 0.02 + float(txn["amount"]) * 5e-5
        assert row["expected_value"] == pytest.approx(expected, abs=tol)


def test_tiny_payments_are_left_alone():
    """Acting has a cost, so trivial amounts must not trigger customer contact."""
    pol = get_policy()
    txn = strip_latent(as_rows(sample_failures(1, seed=17))[0])
    txn["amount"] = 3.0
    top, _ = pol.decide(txn)
    assert top["strategy"] == "no_action"


def test_batch_scoring_matches_single_scoring():
    """The sandbox's fast path must be arithmetically identical to the slow one."""
    pol = get_policy()
    txns = [strip_latent(r) for r in as_rows(sample_failures(6, seed=19))]
    batched = pol.score_batch(txns)
    for txn, ranked in zip(txns, batched):
        single = pol.score(txn)
        assert [r["p_success"] for r in single] == [r["p_success"] for r in ranked]


def test_candidate_grid_spans_both_regimes():
    delays = sorted({c["delay_min"] for c in CANDIDATES})
    assert min(delays) == 0 and max(delays) >= 1440


# --------------------------------------------------------------------------
# reproducibility
# --------------------------------------------------------------------------
def test_scenarios_are_deterministic():
    """The reported number must be reproducible, or it is not a measurement."""
    a = whatif.simulate({"MAX_ATTEMPTS": 5})
    b = whatif.simulate({"MAX_ATTEMPTS": 5})
    assert a["scenario"] == b["scenario"]


def test_sandbox_cannot_disable_the_risk_rule():
    """Even with every tunable at its most permissive, fraud blocks still hold."""
    before = guard.NEVER_RETRY.copy()
    whatif.simulate({"MAX_ATTEMPTS": 6, "MIN_SPACING_MIN": 0,
                     "HUMAN_REVIEW_AMOUNT": 100_000})
    assert guard.NEVER_RETRY == before
    out = guard.validate(_txn(reason="risk_block"),
                         {"strategy": "instant_retry", "delay_min": 0, "channel": "none"})
    assert out["guard"]["verdict"] == "BLOCK"


def test_guard_constants_are_restored_after_a_scenario():
    """A leaked override would silently corrupt every later decision."""
    before = whatif.defaults()
    whatif.simulate({"MAX_ATTEMPTS": 6, "DND_START": 23})
    assert whatif.defaults() == before
