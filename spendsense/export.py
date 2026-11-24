"""Safe CSV export."""

from __future__ import annotations

import pandas as pd

# Spreadsheet apps treat cells starting with these as formulas (CSV injection).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralize(value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def to_safe_csv(df: pd.DataFrame) -> str:
    """CSV text with formula-like text cells neutralized. Numbers are left alone."""
    out = df.copy()
    for col in [c for c in out.columns if not pd.api.types.is_numeric_dtype(out[c])]:
        out[col] = out[col].map(neutralize)
    return out.to_csv(index=False)
