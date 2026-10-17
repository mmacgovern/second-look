"""Safe templates rendered directly from the Facts JSON, with no AI.

Used when the AI draft fails review twice, a budget limit is hit, or the AI
is unavailable. Every number is copied from the facts as-is, and the wording
contains no advice, so it always passes the verifier's Python checks.
"""

from __future__ import annotations

from secondlook.agents.base import Fluency
from secondlook.agents.explainer import Card, ExplainerOutput
from secondlook.agents.pause import PauseOutput

WORDS: dict[str, dict[str, str]] = {
    "new": {
        "company_title": "One company shows up a lot",
        "company": "{name} makes up {pct}% of everything you own, about ${usd}, once you count the shares inside your funds.",
        "direct": " Of that, {direct}% is shares you own directly.",
        "fees_title": "What your funds charge",
        "fees": "Your funds charge about {wer}% a year, or about ${annual} a year right now. Over {years} years at the assumed growth rate, that adds up to about ${cost}.",
        "crash_title": "How a past crash would have felt",
        "crash": "If the {scenario} happened again with today's mix, the worst point would have been a drop of {dd}%, about ${dd_usd}.",
        "recover": " Getting back to the old high took about {months} months.",
        "plan_title": "Your retirement goal",
        "plan": "In the middle scenario, growing {net}% a year after inflation and fees, the projection at retirement is about ${proj}. The goal is about ${target}.",
    },
    "comfortable": {
        "company_title": "Your largest company exposure",
        "company": "Counting shares held inside your funds, {name} is {pct}% of the portfolio (${usd}).",
        "direct": " {direct}% of that is held directly.",
        "fees_title": "Fund fees",
        "fees": "Your weighted expense ratio is {wer}%, about ${annual} a year today. Over {years} years at the assumed return, fees cost about ${cost}.",
        "crash_title": "Crash replay",
        "crash": "With today's weights, the {scenario} would have meant a worst fall of {dd}% (${dd_usd}).",
        "recover": " Recovery took about {months} months from the bottom.",
        "plan_title": "Retirement projection",
        "plan": "In the middle scenario ({net}% a year after inflation and fees), the projected balance at retirement is ${proj} against a target of ${target}.",
    },
    "pro": {
        "company_title": "Look-through concentration",
        "company": "Largest look-through exposure: {name} at {pct}% (${usd}).",
        "direct": " Direct holding: {direct}%.",
        "fees_title": "Expense drag",
        "fees": "Weighted expense ratio {wer}% (${annual}/yr). Expense drag over {years} years at the assumed gross return: ${cost}.",
        "crash_title": "Max drawdown replay",
        "crash": "Constant-weight replay of the {scenario}: max drawdown {dd}% (${dd_usd}).",
        "recover": " Trough to recovery: {months} months.",
        "plan_title": "Retirement projection",
        "plan": "Middle scenario, {net}% net real return: projected ${proj} at retirement vs. a ${target} target.",
    },
}

QUESTIONS: dict[str, list[str]] = {
    "new": [
        "Does the way my money is spread out fit my goals and my timeline?",
        "Are there lower-cost ways to own what I own now?",
        "How would you test my retirement plan, and what would you change in it?",
    ],
    "comfortable": [
        "How does my overall mix compare with what you would expect for my goals and timeline?",
        "Are there lower-cost ways to get the exposure I have now?",
        "Which assumptions in my retirement projection would you change, and why?",
    ],
    "pro": [
        "How would you assess my look-through concentration against my risk capacity?",
        "Where could I reduce expense drag without changing my overall exposure?",
        "What return and withdrawal assumptions would you use to stress-test this plan?",
    ],
}


def _usd(value) -> str:
    return f"{int(value):,}"


def render_template(facts: dict, fluency: Fluency = "comfortable") -> ExplainerOutput:
    w = WORDS[fluency]
    cards: list[Card] = []

    companies = (facts.get("look_through") or {}).get("top_companies") or []
    if companies:
        c = companies[0]
        body = w["company"].format(name=c["name"], pct=c["pct"], usd=_usd(c["usd"]))
        keys = ["look_through.top_companies[0].pct", "look_through.top_companies[0].usd"]
        direct = (c.get("via_pct") or {}).get("direct")
        if direct:
            body += w["direct"].format(direct=direct)
            keys.append("look_through.top_companies[0].via_pct.direct")
        cards.append(Card(title=w["company_title"], body=body, fact_keys=keys))

    fees = facts.get("fees") or {}
    if fees.get("projection"):
        last = len(fees["projection"]) - 1
        p = fees["projection"][last]
        cards.append(Card(
            title=w["fees_title"],
            body=w["fees"].format(wer=fees["weighted_expense_ratio_pct"], annual=_usd(fees["annual_fees_usd"]),
                                  years=p["years"], cost=_usd(p["fees_cost_usd"])),
            fact_keys=["fees.weighted_expense_ratio_pct", "fees.annual_fees_usd", f"fees.projection[{last}].fees_cost_usd"],
        ))

    replays = facts.get("crash_replays") or []
    if replays:
        r = replays[0]
        body = w["crash"].format(scenario=r["scenario"], dd=r["max_drawdown_pct"], dd_usd=_usd(r["max_drawdown_usd"]))
        if r.get("months_to_recover") is not None:
            body += w["recover"].format(months=r["months_to_recover"])
        cards.append(Card(title=w["crash_title"], body=body,
                          fact_keys=["crash_replays[0].max_drawdown_pct", "crash_replays[0].max_drawdown_usd"]))

    plan = facts.get("retirement_plan")
    if plan and plan.get("status") in ("retired", "retiring_now") and "counted_balance_usd" in plan:
        cards.append(Card(
            title=w["plan_title"],
            body=f"The balance counted toward retirement is ${_usd(plan['counted_balance_usd'])}, "
                 f"against a target of ${_usd(plan['target_nest_egg_usd'])}.",
            fact_keys=["retirement_plan.counted_balance_usd", "retirement_plan.target_nest_egg_usd"],
        ))
    elif plan and plan.get("scenarios"):
        i = 1 if len(plan["scenarios"]) > 1 else 0
        s = plan["scenarios"][i]
        cards.append(Card(
            title=w["plan_title"],
            body=w["plan"].format(net=s["net_return_pct"], proj=_usd(s["projected_at_retirement_usd"]),
                                  target=_usd(plan["target_nest_egg_usd"])),
            fact_keys=[f"retirement_plan.scenarios[{i}].projected_at_retirement_usd", "retirement_plan.target_nest_egg_usd"],
        ))

    if not cards:
        cards.append(Card(title="Summary", body="Your file was read, but there was not enough data for a summary.", fact_keys=[]))
    return ExplainerOutput(cards=cards[:4], advisor_questions=QUESTIONS[fluency])


REPLAY_NAMES = {"gfc": "2008-09 crisis", "covid": "Covid crash", "y2022": "2022 decline"}


def render_pause_template(pause_facts: dict) -> PauseOutput:
    before, after = pause_facts["before"], pause_facts["after"]
    points = [f"Cash would go from {before['cash_pct']}% to {after['cash_pct']}% of the portfolio."]
    for key, dd in before.get("max_drawdown_pct", {}).items():
        points.append(
            f"In the {REPLAY_NAMES.get(key, key)} replay, the worst fall would be {dd}% "
            f"(${_usd(before['max_drawdown_usd'][key])}) before and {after['max_drawdown_pct'].get(key)}% "
            f"(${_usd(after['max_drawdown_usd'].get(key, 0))}) after."
        )
    for e in pause_facts.get("market_history", [])[:2]:
        text = f"In the {e['event']}, the market fell {e['peak_to_bottom_drop_pct']}% from its peak to the bottom."
        if e.get("gain_1y_after_bottom_pct") is not None:
            text += f" One year after the bottom it was up {e['gain_1y_after_bottom_pct']}% from that low."
        if e.get("months_bottom_to_recovery") is not None:
            text += f" It took {e['months_bottom_to_recovery']} months to get back to the old peak."
        points.append(text)
    action = pause_facts["proposed_action"]
    action = action[:1].lower() + action[1:]  # quoted, not an instruction ("sell all of ...")
    summary = (f"This check looks at one possible change, to {action}. "
               "It shows what would be different, not whether to do it.")
    return PauseOutput(summary=summary, points=points[:6])
