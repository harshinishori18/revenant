# Revenant

**Agentic revenue recovery for failed payments.**
Razorpay Buildathon — Track 3: AI Revenue Recovery.

> Every failed payment is a decision, not a loss. The question is never *"should
> we retry?"* — it is *when*, *through which channel*, and *whether we are
> allowed to at all*.

---

## The problem, stated precisely

When a payment fails, most recovery systems apply one fixed rule: retry after
N minutes, up to three times. That rule is provably wrong, and here is why.

Different failure reasons have **opposite optimal retry delays**:

| Failure reason | What is actually happening | Optimal delay |
|---|---|---|
| `network_drop` | The customer's session is still open, but decaying | **Minutes.** Every minute of delay burns conversion. |
| `wrong_otp` | User error, customer still present | **Minutes.** |
| `bank_timeout` / `gateway_error` | Issuer-side impairment | **Until it clears** — useless before, high after. |
| `insufficient_funds` | The account is empty right now | **Hours.** A 3-minute retry is guaranteed waste. |
| `risk_block` | Fraud / AML decline | **Never.** Retrying is a compliance violation. |

Any single fixed interval is wrong for at least two of these at all times.
That gap is the recoverable revenue, and Revenant measures exactly how much of
it a learned policy can capture.

---

## Measured results

Held-out batch of **8,000 failed payments** (₹10,266,286 at risk) the model
never saw in training. Three policies, **the same batch, the same latent state,
the same random stream** — a paired comparison, so the difference between them
is attributable to the policy and not to sampling noise.

| Policy | Recovered | Recovery rate | 95% CI (2,000 bootstrap resamples) | Attempts | Nudges | Blocked | Escalated |
|---|---:|---:|---|---:|---:|---:|---:|
| Do nothing | ₹0 | 0.00% | — | 0 | 0 | 0 | 0 |
| Fixed retry T+30, cap 3 | ₹3,727,560 | 42.44% | ₹3.55M – ₹3.91M | 13,806 | 0 | 4,560 | 45 |
| **Revenant** | **₹6,404,173** | **68.53%** | ₹6.19M – ₹6.62M | **10,642** | 8,342 | 2,471 | 45 |

**+₹2,676,613 recovered (+71.8%) using 22.9% fewer retry attempts.**

More money *and* less customer contact. That combination is the result — either
one alone would be easy.

### Ablation — which layer is doing the work?

A headline number invites the question *"would something simpler have done just
as well?"*. Removing one capability at a time (3,000-payment subsample):

| Variant | Recovered | Cost of removing it |
|---|---:|---:|
| Full system | ₹2,402,688 | — |
| Reason code hidden from model | ₹1,734,159 | −₹668,529 |
| Delay pinned to 30 min | ₹1,975,260 | −₹427,429 |
| No customer nudges | ₹2,205,341 | −₹197,347 |
| Action cost ignored | ₹2,398,312 | −₹4,377 |

Timing and reason-awareness carry the result. Cost-awareness barely moves the
money — but it is what stops the system from messaging customers it has no
business messaging, which is why it stays in.

### Model card

Single LightGBM classifier, `P(success | context, reason, action)`:

| Metric | Value |
|---|---|
| Holdout rows | 12,000 |
| ROC AUC (single holdout) | 0.833 |
| ROC AUC (5-fold CV) | 0.8321 ± 0.0013 |
| Brier score | 0.144 |
| Brier skill vs. base-rate predictor | 0.274 |
| Log loss | 0.432 |
| Observed base rate | 0.274 |
| Mean predicted | 0.269 |

Cross-validated because a single split can flatter a model by luck. The per-fold
spread is 0.8301–0.8337, so the result is a property of the model rather than of
one lucky cut. The Brier skill score is the honest headline: 0.144 means nothing
on its own, but it is 27.4% better than always predicting the average.

Calibration matters more than AUC here, because expected value is a probability
multiplied by rupees. A miscalibrated model produces confidently wrong money.

Highest-gain feature: **`log_delay`** — the retry delay itself. The model
learned that *when* you retry matters more than anything else about the payment,
which is the thesis of the product, recovered from data rather than asserted.

---

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │   Merchant dashboard  (static, no build) │
                    │   ledger · thesis · console · model card │
                    └────────────────────┬─────────────────────┘
                                         │  REST
                    ┌────────────────────▼─────────────────────┐
                    │            FastAPI  (server/)            │
                    └────────────────────┬─────────────────────┘
                                         │
        ┌────────────────────────────────┼────────────────────────────┐
        │                                │                            │
┌───────▼────────┐            ┌──────────▼──────────┐      ┌──────────▼─────────┐
│ Policy         │            │ LLM agent           │      │ Compliance guard   │
│ optimiser      │            │ (tie-breaker only)  │      │                    │
│                │            │                     │      │ stopping rules     │
│ sweeps 51      │  ambiguous │ picks among the     │      │ DND hours          │
│ candidate      │───────────▶│ optimiser's ranked  │─────▶│ escalation         │
│ actions,       │  (top two  │ shortlist, adds a   │      │ delay ceilings     │
│ EV argmax      │  within 8%)│ merchant rationale  │      │                    │
└───────┬────────┘            └─────────────────────┘      └──────────┬─────────┘
        │                                                             │
        │  ~5 ms, ~87% of volume                                      │ verdict
        └─────────────────────────────────────────────────────────────┤
                                                                      ▼
                                                        SQLite audit trail
                                                        (immutable, per decision)
```

**Three design positions worth defending:**

1. **The LLM does not invent actions.** It selects from a ranked, EV-scored
   shortlist the model has already produced, and contributes the thing a
   gradient-boosted tree cannot: judgement on customer experience when the money
   is a tie, plus a sentence a merchant can read. Letting a language model
   originate financial actions would be an architecture, not a good one.

2. **The LLM is invoked only when it can add something.** If the top two actions
   differ by more than 8% expected value, there is nothing to deliberate about
   and a ~900 ms API call would be waste. Roughly 87% of volume is decided by the
   optimiser in single-digit milliseconds.

3. **Compliance is code, not a prompt.** A prompt is a request. `core/guard.py`
   is a control. Every action — from the optimiser or the agent — passes through
   it, and it can `PASS`, `MODIFY`, `BLOCK` or `ESCALATE`. If the LLM proposes a
   WhatsApp nudge at 01:40, the guard defers it to 09:00 and writes down why.

---

## Repository layout

```
revenant/
├── core/
│   ├── sim.py          counterfactual environment (latent state, outcome function)
│   ├── policy.py       candidate sweep + expected-value argmax
│   ├── guard.py        compliance rules, verdicts, audit records
│   ├── agent.py        LLM tie-breaker (bounded tool schema, safe fallback)
│   ├── copilot.py      question answering over the audit trail, via tool calls
│   └── whatif.py       policy sandbox — batched counterfactual re-runs
├── scripts/
│   ├── generate_data.py   logged history under a randomised legacy policy
│   ├── train_model.py     action-conditioned model + calibration report
│   ├── evaluate.py        paired three-policy ledger with bootstrap CIs
│   └── ablation.py        capability-removal study
├── tests/              20 invariant tests — guard rules, leakage, determinism
├── server/main.py      FastAPI: decisions, audit trail, ledger, curves
├── web/                dashboard — plain HTML/CSS/JS, hand-rolled SVG charts
├── artifacts/          trained model + audit.db  (generated)
├── data/               train_log.csv, eval_batch.csv  (generated)
└── results/            ledger.json, model_card.json, ablation.json  (generated)
```

---

## Running it

Requires Python 3.10+. No Node, no build step, no database server.

```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt

python -m scripts.generate_data     # ~15 s   builds train + held-out batches
python -m scripts.train_model       # ~30 s   trains and reports calibration
python -m scripts.evaluate          # ~2 min  produces the headline ledger
python -m scripts.ablation          # ~5 min  optional

python -m pytest tests/ -q          # 20 invariant tests

uvicorn server.main:app --reload --reload-dir server --reload-dir core --reload-dir web --port 8000
```

Open **http://localhost:8000**.

Scoping `--reload-dir` matters: reloading on the whole tree includes `venv/`,
and any package install triggers a reload storm.

AI answering is optional. Without a key the system runs fully and answers from a
deterministic router; with one, questions route to a tool-calling agent.

```bash
cp .env.example .env                # then add GROQ_API_KEY
```

Groq is the default provider — free, no credit card, OpenAI-compatible, and it
supports function calling, which the Copilot depends on. `ANTHROPIC_API_KEY` is
used instead if that is the key present.

### API

| Endpoint | Purpose |
|---|---|
| `POST /api/analyze` | One failed payment → full decision chain, audited |
| `GET /api/audit/{txn_id}` | The immutable decision record |
| `GET /api/ledger` | Measured counterfactual results |
| `GET /api/model-card` | Holdout metrics and calibration curve |
| `GET /api/rulebook` | The compliance rules in force |
| `GET /api/curve-by-reason` | P(success) vs. delay, per failure reason |
| `POST /api/copilot` | Plain-English question → answer, with the lookups it ran |
| `GET /api/copilot/mode` | Whether AI answering is active, and why not if it is not |
| `POST /api/whatif` | Re-run the simulation with changed compliance settings |

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"txn_id":"demo_1","hour":2,"amount":5400,"method":"Card","bank":"HDFC",
       "device":"web","network_quality":"good","prior_attempts":0,"reason":"risk_block"}'
```

Returns a `BLOCK` with code `RISK_BLOCK_NO_RETRY` — the guard refusing an action
the optimiser was never allowed to take.

---

## Interactive layers

Two things separate a dashboard a judge watches from a product a judge can probe.

### Recovery Copilot

Ask questions in plain English. The Copilot answers by **calling real functions**
against the live audit database and the policy sandbox — it cannot state a number
it did not retrieve, and the lookups it ran are named beneath each answer.

It works with no API key. A scored intent router maps the question onto the same
eight tool functions the language model would call, so the panel always answers.
With `GROQ_API_KEY` set, every question routes to a tool-calling agent instead;
if that call is slow or fails, the deterministic path answers rather than leaving
a spinner running. Model fallback is built in — free tiers retire models without
warning, so three are tried in order.

### Policy sandbox

Move a compliance setting and measure what it does to the money. Every scenario
replays the same payments under the new rule, so the answer is measured rather
than asserted. `RISK_BLOCK_NO_RETRY` is deliberately not adjustable — some rules
are business preferences and some are not, and a sandbox has to know which is which.

Representative measured results (600-payment sample):

| Change | Effect |
|---|---:|
| Retry limit 3 → 5 | +₹82,113 (+18.9%), 313 more attempts, ~₹262 per extra attempt |
| Manual review ceiling ₹10k → ₹50k | +₹39,490 (+9.1%) |
| Minimum gap 15 → 90 min | −₹10,000 (−2.3%) |

**Performance note.** The first implementation scored one payment at a time —
~2,200 model calls per scenario, ~8 seconds, and it blocked the API worker, which
also stalled any Copilot question asked meanwhile. Payments within an attempt
round are independent, so scoring is now round-based and batched: four model calls
per scenario instead of thousands. Scenarios land in under two seconds, run in a
threadpool off the event loop, and are hard-bounded by a timeout. Measured under
concurrent load, a Copilot question returns in ~10 ms while a scenario is running.

---

### Test suite

`pytest tests/ -q` — 20 tests, each pinning an invariant that would invalidate
the result if it broke silently. Among them: fraud declines cannot be retried
under any strategy; **the sandbox cannot disable that rule** even with every
setting at its most permissive; guard constants are restored after a scenario so
an override cannot leak; latent state provably does not reach the model; the
opposite-delay thesis is asserted against the data rather than assumed; and
scenarios are deterministic, so the headline number is reproducible.

---

## Limitations, stated plainly

- **The transaction environment is synthetic.** No participant has access to real
  Razorpay data. The failure mix, amount distribution and hourly traffic curve
  are calibrated from published PSP ranges; the recovery dynamics are modelled
  explicitly rather than learned from production behaviour. Every number in this
  README is measured *within that environment* and should be read as a
  demonstration that the method works, not as a forecast of production lift.

- **Why a simulator at all.** Counterfactual evaluation is impossible on logs
  alone — you only observe the action you took. A simulator is the only way to
  produce an honest paired comparison at this stage. With production logs, the
  simulator is replaced by **doubly-robust off-policy evaluation** on the real
  data; `core/policy.py`, `core/guard.py` and the audit layer are unchanged,
  and only `scripts/evaluate.py` swaps its estimator.

- **Nudge delivery is simulated.** SMS/WhatsApp/email dispatch requires provider
  credentials. The decision, the channel selection, the DND enforcement and the
  audit record are all real; only the outbound send is stubbed.

- **The guard's thresholds are hard-coded.** A production deployment would load
  them per merchant, and the DND window would follow the customer's actual
  jurisdiction rather than a single IST assumption.

- **Retry fatigue is modelled as a fixed decay.** In reality it is
  customer-specific and would itself be learned.

- **The LLM's contribution is small by design, and small in fact.** It breaks
  ties on roughly 10–15% of decisions and the ablation shows the result barely
  moves without it. That is the honest reading: the money comes from the timing
  model and the reason code, not from the language model.

## What I would build next

1. Doubly-robust off-policy evaluation against a real logged policy.
2. Per-merchant guard configuration with a policy-change audit log.
3. Thompson sampling over the action grid, so the system keeps exploring
   instead of locking onto the policy its first model happened to prefer.
4. A recovered-revenue attribution report that survives a finance team's audit.
