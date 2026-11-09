"""The full flow: clean -> rules -> mask -> AI -> review flags.

    CSV -> parser -> merchant cleanup -> rules (cheap, predictable)
                                     -> unmatched: mask PII -> Claude -> validate
        -> anything below the confidence threshold is flagged for the user
"""

from __future__ import annotations

import pandas as pd

from .cleaning import normalize_merchant
from .llm import categorize_with_llm
from .pii import mask_pii
from .rules import categorize_by_rules

DEFAULT_THRESHOLD = 0.7


def categorize(df: pd.DataFrame, client=None, threshold: float = DEFAULT_THRESHOLD,
               cache: dict | None = None) -> pd.DataFrame:
    """Add merchant, category, confidence, source and needs_review columns.

    `cache` maps a merchant name to an earlier AI result, so repeat merchants
    (the same coffee shop every week) are only sent to the model once.
    """
    out = df.copy()
    out["merchant"] = out["description"].map(normalize_merchant)
    out["category"] = None
    out["confidence"] = 0.0
    out["source"] = None
    cache = cache if cache is not None else {}

    # 1) Rules first: free, instant and predictable.
    for idx, row in out.iterrows():
        category = categorize_by_rules(row["description"])
        if category:
            out.loc[idx, ["category", "confidence", "source"]] = [category, 1.0, "rule"]

    # 2) Reuse cached AI answers for merchants we've already seen.
    pending = out[out["source"].isna()]
    to_send: dict[str, str] = {}
    for idx, row in pending.iterrows():
        merchant = row["merchant"]
        if merchant in cache:
            hit = cache[merchant]
            out.loc[idx, ["category", "confidence", "source"]] = [hit["category"], hit["confidence"], hit["source"]]
        else:
            to_send.setdefault(merchant, mask_pii(row["description"]))

    # 3) One AI call per unique unknown merchant, never the raw description.
    merchants = list(to_send)
    ai_results = categorize_with_llm([(i, to_send[m]) for i, m in enumerate(merchants)], client=client)
    for i, merchant in enumerate(merchants):
        cache[merchant] = ai_results[i]
    for idx, row in out[out["source"].isna()].iterrows():
        hit = cache[row["merchant"]]
        out.loc[idx, ["category", "confidence", "source"]] = [hit["category"], hit["confidence"], hit["source"]]

    # 4) Money coming in that the AI couldn't place is almost always income or a refund.
    unknown_credit = (out["amount"] > 0) & (out["source"] == "fallback")
    out.loc[unknown_credit, ["category", "confidence", "source"]] = ["Income", 0.5, "fallback"]

    out["confidence"] = out["confidence"].astype(float)
    out["needs_review"] = out["confidence"] < threshold
    return out


def apply_corrections(df: pd.DataFrame, corrections: dict[str, str]) -> pd.DataFrame:
    """Apply user corrections (merchant -> category) to every matching row."""
    out = df.copy()
    for merchant, category in corrections.items():
        mask = out["merchant"] == merchant
        out.loc[mask, ["category", "confidence", "source", "needs_review"]] = [category, 1.0, "user", False]
    return out
