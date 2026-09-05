"""
server/main.py — Revenant API.

    GET  /                      the merchant dashboard (static)
    GET  /api/health            liveness + whether the LLM agent is configured
    GET  /api/ledger            measured counterfactual results (results/ledger.json)
    GET  /api/model-card        holdout metrics, calibration curve, feature gains
    GET  /api/rulebook          the compliance rules the guard enforces
    GET  /api/sample?n=         n held-out failed payments (observed fields only)
    POST /api/analyze           one payment -> full decision chain, audited
    GET  /api/audit/{txn_id}    the immutable decision record
    GET  /api/audit?limit=      recent decision records
    POST /api/curve             P(success) vs retry delay for this payment
    GET  /api/curve-by-reason   the signature chart: opposite optimal delays

Decision path, and this is a deliberate cost decision worth stating:
the optimiser decides every payment in single-digit milliseconds. The LLM agent
is invoked only when the top two viable actions are within 8% expected value of
each other. Everywhere else, paying ~900 ms and an API call to re-derive an
answer the model already has would be waste, not intelligence.

Run:  uvicorn server.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import asyncio
import threading
from contextlib import closing

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core import agent, copilot, guard, whatif
from core.policy import get_policy

load_dotenv()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
RESULTS = os.path.join(ROOT, "results")
DB_PATH = os.path.join(ROOT, "artifacts", "audit.db")

app = FastAPI(title="Revenant API", version="1.0.0",
              description="Agentic revenue recovery with a compliance guard.")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


# --------------------------------------------------------------------------
# audit store
# --------------------------------------------------------------------------
def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id TEXT, ts TEXT, verdict TEXT, source TEXT,
            amount REAL, payload TEXT)""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_txn ON decisions(txn_id)")
        con.commit()


def write_audit(rec: dict) -> None:
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.execute("INSERT INTO decisions (txn_id, ts, verdict, source, amount, payload)"
                    " VALUES (?,?,?,?,?,?)",
                    (rec["txn_id"], rec["ts"], rec["guard_verdict"], rec["decision_source"],
                     rec["amount"], json.dumps(rec, default=str)))
        con.commit()


@app.on_event("startup")
def _startup() -> None:
    init_db()
    get_policy()          # fail fast at boot if the model artifact is missing
    _load_sample_pool()
    # Warm the sandbox baseline off the request path. Without this the first
    # scenario a user runs also pays for computing the comparison point, which
    # is what made the sandbox feel broken rather than merely slow.
    threading.Thread(target=whatif.warm, daemon=True).start()


# --------------------------------------------------------------------------
# held-out sample pool
# --------------------------------------------------------------------------
_POOL: list[dict] = []
OBSERVED = ["txn_id", "hour", "amount", "method", "bank", "device",
            "network_quality", "prior_attempts", "reason"]


def _load_sample_pool() -> None:
    global _POOL
    import pandas as pd
    path = os.path.join(ROOT, "data", "eval_batch.csv")
    if not os.path.exists(path):
        _POOL = []
        return
    df = pd.read_csv(path)[OBSERVED]
    _POOL = df.to_dict("records")


class Txn(BaseModel):
    txn_id: str = Field(..., examples=["eval_000001"])
    hour: int = Field(..., ge=0, le=23)
    amount: float = Field(..., gt=0)
    method: str
    bank: str
    device: str
    network_quality: str
    prior_attempts: int = 0
    reason: str


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok",
            "model": guard.MODEL_VERSION,
            # Ask the copilot which provider it can actually use. Checking one
            # vendor's env var here was a bug: a valid Groq key still reported
            # "built-in answers" because only Anthropic was being looked for.
            **copilot.get_mode(),
            "agent_configured": copilot.get_mode()["key_configured"],
            "sample_pool": len(_POOL)}


@app.get("/api/ledger")
def ledger():
    path = os.path.join(RESULTS, "ledger.json")
    if not os.path.exists(path):
        raise HTTPException(404, "run: python -m scripts.evaluate")
    return json.load(open(path))


@app.get("/api/model-card")
def model_card():
    path = os.path.join(RESULTS, "model_card.json")
    if not os.path.exists(path):
        raise HTTPException(404, "run: python -m scripts.train_model")
    return json.load(open(path))


@app.get("/api/rulebook")
def rulebook():
    return {"rules": [{"code": c, "text": t} for c, t in guard.RULEBOOK]}


@app.get("/api/sample")
def sample(n: int = Query(24, ge=1, le=500), seed: int | None = None):
    import random
    if not _POOL:
        raise HTTPException(404, "run: python -m scripts.generate_data")
    rnd = random.Random(seed)
    return {"transactions": rnd.sample(_POOL, min(n, len(_POOL)))}


@app.post("/api/analyze")
def analyze(txn: Txn):
    t0 = time.perf_counter()
    payload = txn.model_dump()
    pol = get_policy()
    top, ranked = pol.decide(payload)

    use_agent = agent.is_ambiguous(ranked) and bool(os.getenv("ANTHROPIC_API_KEY"))
    if use_agent:
        proposed = agent.decide(payload, ranked)
        source = "llm_agent" if proposed.get("agent_ok") else "optimiser_fallback"
        if not proposed.get("agent_ok"):
            proposed["rationale"] = _explain(payload, top, len(ranked))
    else:
        proposed = {k: top[k] for k in ("strategy", "delay_min", "channel")}
        proposed["rationale"] = _explain(payload, top, len(ranked))
        source = "policy_optimiser"

    final = guard.validate(payload, proposed)
    if not final.get("rationale"):
        final["rationale"] = proposed.get("rationale")
    elif final["guard"]["verdict"] == "MODIFY":
        final["rationale"] = proposed.get("rationale")

    latency = round((time.perf_counter() - t0) * 1000, 1)
    rec = guard.audit_record(payload, ranked, proposed, final, source, latency)
    rec["ambiguous"] = agent.is_ambiguous(ranked)
    rec["best_ev"] = top["expected_value"]
    write_audit(rec)
    return rec


def _explain(txn: dict, top: dict, n_candidates: int) -> str:
    if top["strategy"] == "no_action":
        return (f"No action: every candidate action has negative expected value on a "
                f"Rs {txn['amount']:,.0f} {txn['reason'].replace('_', ' ')} failure — "
                "the attempt would cost more than it is worth.")
    when = ("immediately" if top["delay_min"] <= 5
            else f"in {int(top['delay_min'])} min" if top["delay_min"] < 120
            else f"in {top['delay_min'] / 60:.0f} h")
    art = "an" if top["channel"][0] in "aeiou" else "a"
    via = "" if top["channel"] == "none" else f" with {art} {top['channel']} nudge"
    return (f"Retry {when}{via}: {top['p_success'] * 100:.0f}% modelled success on a "
            f"{txn['reason'].replace('_', ' ')} failure, the highest expected value "
            f"(Rs {top['expected_value']:,.0f}) of {n_candidates} candidate actions.")


class WhatIf(BaseModel):
    MAX_ATTEMPTS: int | None = None
    MIN_SPACING_MIN: int | None = None
    DND_START: int | None = None
    DND_END: int | None = None
    HUMAN_REVIEW_AMOUNT: float | None = None


@app.get("/api/whatif/defaults")
def whatif_defaults():
    return {**whatif.schema(), "ready": whatif.is_ready()}


@app.post("/api/whatif")
async def whatif_run(cfg: WhatIf):
    overrides = {k: v for k, v in cfg.model_dump().items() if v is not None}
    if not overrides:
        raise HTTPException(400, "Change at least one setting before running a scenario.")
    # Off the event loop so a scenario never blocks the Copilot or the console,
    # and hard-bounded so a pathological run surfaces as an error rather than a
    # spinner that never resolves.
    try:
        return await asyncio.wait_for(run_in_threadpool(whatif.simulate, overrides),
                                      timeout=90)
    except asyncio.TimeoutError:
        raise HTTPException(504, "Scenario timed out. Try changing one setting at a time.")


class Question(BaseModel):
    question: str = Field(..., min_length=2, max_length=600)


@app.get("/api/copilot/mode")
def copilot_mode():
    return copilot.get_mode()


@app.post("/api/copilot")
async def copilot_ask(q: Question):
    """Always returns an answer.

    The Copilot runs off the event loop and is hard-bounded. If the language
    model is slow or unreachable, the deterministic router answers from the same
    tool functions instead — the panel never hangs, which matters more in a live
    demo than the extra fluency does.
    """
    try:
        return await asyncio.wait_for(run_in_threadpool(copilot.ask, q.question),
                                      timeout=45)
    except asyncio.TimeoutError:
        fallback = await run_in_threadpool(copilot.answer_offline, q.question)
        fallback["mode"] = "deterministic (agent timed out)"
        return fallback
    except Exception as exc:  # noqa: BLE001
        return {"answer": f"I could not complete that lookup ({type(exc).__name__}). "
                          "Try rephrasing, or ask about the ledger or recent decisions.",
                "tool_calls": [], "mode": "error"}


@app.get("/api/audit/{txn_id}")
def audit_one(txn_id: str):
    with closing(sqlite3.connect(DB_PATH)) as con:
        row = con.execute("SELECT payload FROM decisions WHERE txn_id=? ORDER BY id DESC LIMIT 1",
                          (txn_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no decision recorded for that transaction")
    return json.loads(row[0])


@app.get("/api/audit")
def audit_recent(limit: int = Query(50, ge=1, le=500)):
    with closing(sqlite3.connect(DB_PATH)) as con:
        rows = con.execute("SELECT payload FROM decisions ORDER BY id DESC LIMIT ?",
                           (limit,)).fetchall()
    return {"records": [json.loads(r[0]) for r in rows]}


@app.post("/api/curve")
def curve(txn: Txn, channel: str = "none"):
    return {"txn_id": txn.txn_id,
            "reason": txn.reason,
            "points": get_policy().delay_curve(txn.model_dump(), channel=channel)}


@app.get("/api/curve-by-reason")
def curve_by_reason(amount: float = 2500.0, hour: int = 20):
    """The signature chart: P(success) vs delay, per failure reason.

    Two of these curves run in opposite directions. That is why a single fixed
    retry interval leaves money on the table, and it is the clearest one-glance
    argument for the whole product.
    """
    pol = get_policy()
    base = {"txn_id": "curve", "hour": hour, "amount": amount, "method": "UPI",
            "bank": "HDFC", "device": "android", "network_quality": "good",
            "prior_attempts": 0}
    reasons = ["network_drop", "wrong_otp", "bank_timeout",
               "insufficient_funds", "gateway_error", "card_declined"]
    return {"amount": amount,
            "series": [{"reason": r, "points": pol.delay_curve({**base, "reason": r})}
                       for r in reasons]}


# --------------------------------------------------------------------------
# static frontend (mounted last so /api/* wins)
# --------------------------------------------------------------------------
if os.path.isdir(WEB):
    @app.get("/")
    def index():
        return FileResponse(os.path.join(WEB, "index.html"))

    app.mount("/", StaticFiles(directory=WEB), name="web")
