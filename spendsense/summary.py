"""Plain-language "where did my money go" summary.

The model only ever sees aggregated totals (category sums, monthly totals,
recurring charges). It never sees individual transactions, so no personal
details can leak into the prompt. If there's no API key, a template summary
is written instead.
"""

from __future__ import annotations

import json
import logging

import pandas as pd

from .llm import DEFAULT_MODEL

log = logging.getLogger(__name__)

SUMMARY_PROMPT = """You are a friendly, practical personal-finance assistant.
Using ONLY the numbers below, write a short summary (4-6 sentences) of where this
person's money went. Mention the top categories, any notable month-to-month
change, and recurring charges worth reviewing. Do not invent numbers, do not
give investment advice, and keep it encouraging rather than judgmental.

Data (CAD):
{data}"""


def build_summary_inputs(totals: dict, categories: pd.DataFrame,
                         monthly: pd.DataFrame, recurring: pd.DataFrame) -> dict:
    return {
        "totals": totals,
        "top_categories": categories.head(6)[["category", "total", "share"]].to_dict("records"),
        "monthly_spending": {m: round(float(v), 2) for m, v in monthly.sum(axis=1).items()},
        "recurring": recurring[["merchant", "typical_amount"]].to_dict("records") if not recurring.empty else [],
    }


def template_summary(inputs: dict) -> str:
    t = inputs["totals"]
    top = inputs["top_categories"]
    parts = [f"You spent ${t['total_spent']:,.2f} over {t['months']} month(s), "
             f"about ${t['avg_monthly_spent']:,.2f} a month."]
    if top:
        lead = ", ".join(f"{c['category']} (${c['total']:,.2f})" for c in top[:3])
        parts.append(f"Your biggest categories were {lead}.")
    monthly = list(inputs["monthly_spending"].items())
    if len(monthly) >= 2:
        (m1, v1), (m2, v2) = monthly[-2], monthly[-1]
        if v1:
            change = (v2 - v1) / v1 * 100
            direction = "up" if change >= 0 else "down"
            parts.append(f"Spending in {m2} was {direction} {abs(change):.0f}% from {m1}.")
    if inputs["recurring"]:
        monthly_recurring = sum(r["typical_amount"] for r in inputs["recurring"])
        parts.append(f"{len(inputs['recurring'])} recurring charges add up to about "
                     f"${monthly_recurring:,.2f} a month, worth a quick review.")
    return " ".join(parts)


def write_summary(inputs: dict, client=None, model: str = DEFAULT_MODEL) -> tuple[str, str]:
    """Return (summary_text, source) where source is 'ai' or 'template'."""
    if client is None:
        return template_summary(inputs), "template"
    try:
        response = client.messages.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user",
                       "content": SUMMARY_PROMPT.format(data=json.dumps(inputs, default=str, indent=2))}],
        )
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
        return (text, "ai") if text else (template_summary(inputs), "template")
    except Exception as err:
        log.warning("AI summary failed, using template: %s", err)
        return template_summary(inputs), "template"
