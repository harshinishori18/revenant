"""
core/agent.py — the LLM tie-breaker.

Design position, and a deliberate one: the LLM does NOT invent an action.

It receives the optimiser's ranked, EV-scored candidate list and chooses among
them. It contributes the thing a gradient-boosted tree cannot: soft business
judgement on cases where the numbers are effectively tied (channel etiquette,
interruption cost, tone) plus a merchant-readable justification.

Cost control: the agent is invoked ONLY when the top two viable actions are
within AMBIGUITY_BAND of each other on expected value — roughly 10–15% of
volume. Everywhere else a ~3 ms model call is strictly better than a ~900 ms
LLM call, and we can say so with numbers.

Safety: whatever it returns still passes through guard.validate(). If the API
key is missing or the call fails, the system falls back to the optimiser's top
action — it degrades to a working state, it never fails open.
"""
from __future__ import annotations

import os

AMBIGUITY_BAND = 0.08

TOOL = {
    "name": "select_recovery_action",
    "description": ("Choose exactly one action from the supplied ranked candidate list "
                    "and justify it in one sentence for a merchant operations analyst."),
    "input_schema": {
        "type": "object",
        "properties": {
            "candidate_index": {"type": "integer",
                                "description": "0-based index into the candidate list provided."},
            "channel": {"type": "string", "enum": ["none", "sms", "whatsapp", "email"],
                        "description": "Override the candidate's channel only if clearly better."},
            "rationale": {"type": "string",
                          "description": "One sentence, plain English, no jargon, under 30 words."},
        },
        "required": ["candidate_index", "rationale"],
    },
}

SYSTEM = (
    "You are the recovery-decision agent inside a payments platform. You choose "
    "between actions that a calibrated model has already scored as near-equivalent "
    "in expected value. Because the money is a tie, decide on customer experience: "
    "prefer fewer interruptions, prefer the cheaper and less intrusive channel when "
    "the lift is comparable, and be more conservative with customers who have already "
    "been contacted. Never propose more attempts than the candidate list allows."
)


def is_ambiguous(ranked: list[dict], band: float = AMBIGUITY_BAND) -> bool:
    live = [r for r in ranked if r["strategy"] != "no_action" and r["expected_value"] > 0]
    if len(live) < 2:
        return False
    a, b = live[0]["expected_value"], live[1]["expected_value"]
    return abs(a - b) <= band * abs(a)


def _fallback(ranked: list[dict], note: str) -> dict:
    top = ranked[0]
    return {"strategy": top["strategy"], "delay_min": top["delay_min"],
            "channel": top["channel"],
            "rationale": f"Optimiser default ({note}).", "agent_ok": False}


def decide(txn: dict, ranked: list[dict], model: str = "claude-sonnet-4-6") -> dict:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return _fallback(ranked, "no API key configured")
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        shortlist = ranked[:6]
        lines = "\n".join(
            f"[{i}] {c['strategy']} | delay={c['delay_min']} min | channel={c['channel']} "
            f"| p_success={c['p_success']:.2f} | EV=Rs {c['expected_value']:,.0f}"
            for i, c in enumerate(shortlist))

        prompt = (
            f"Failed payment: Rs {float(txn['amount']):,.0f} via {txn['method']} "
            f"({txn['bank']}, {txn['device']}).\n"
            f"Gateway failure reason: {txn['reason']}.\n"
            f"Attempts already made: {txn.get('prior_attempts', 0)}. "
            f"Local hour: {txn['hour']}:00. Network at failure: {txn['network_quality']}.\n\n"
            f"These candidate actions are within noise of each other on expected value:\n{lines}\n\n"
            "Select one and give a one-sentence rationale."
        )

        resp = client.messages.create(
            model=model, max_tokens=400, system=SYSTEM, tools=[TOOL],
            tool_choice={"type": "tool", "name": "select_recovery_action"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                i = max(0, min(int(block.input["candidate_index"]), len(shortlist) - 1))
                chosen = shortlist[i]
                return {"strategy": chosen["strategy"],
                        "delay_min": chosen["delay_min"],
                        "channel": block.input.get("channel", chosen["channel"]),
                        "rationale": block.input["rationale"].strip(),
                        "agent_ok": True}
        return _fallback(ranked, "agent returned no tool call")
    except Exception as exc:  # noqa: BLE001 — never let the agent break the pipeline
        return _fallback(ranked, f"agent unavailable: {type(exc).__name__}")
