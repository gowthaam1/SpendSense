"""Numbers for the dashboard. Plain pandas, no AI involved."""

from __future__ import annotations

import pandas as pd

NOT_SPENDING = {"Income", "Transfers"}


def spending(df: pd.DataFrame) -> pd.DataFrame:
    """Money going out, excluding transfers, as positive numbers."""
    out = df[(df["amount"] < 0) & (~df["category"].isin(NOT_SPENDING))].copy()
    out["spent"] = -out["amount"]
    return out


def totals(df: pd.DataFrame) -> dict:
    spent = spending(df)["spent"].sum()
    income = df.loc[(df["amount"] > 0) & (df["category"] == "Income"), "amount"].sum()
    months = max(df["date"].dt.to_period("M").nunique(), 1)
    return {
        "total_spent": round(float(spent), 2),
        "total_income": round(float(income), 2),
        "net": round(float(income - spent), 2),
        "months": int(months),
        "avg_monthly_spent": round(float(spent / months), 2),
        "transactions": int(len(df)),
        "needs_review": int(df["needs_review"].sum()) if "needs_review" in df else 0,
    }


def by_category(df: pd.DataFrame) -> pd.DataFrame:
    s = spending(df)
    table = (s.groupby("category")["spent"].agg(total="sum", count="count")
              .sort_values("total", ascending=False).reset_index())
    grand = table["total"].sum()
    table["share"] = (table["total"] / grand).round(3) if grand else 0.0
    table["total"] = table["total"].round(2)
    return table


def by_month(df: pd.DataFrame) -> pd.DataFrame:
    s = spending(df)
    s["month"] = s["date"].dt.to_period("M").astype(str)
    return (s.pivot_table(index="month", columns="category", values="spent",
                          aggfunc="sum", fill_value=0).round(2))


def recurring_charges(df: pd.DataFrame, min_months: int = 3, tolerance: float = 0.10) -> pd.DataFrame:
    """Find charges that repeat every month at roughly the same amount.

    A merchant counts as recurring when it appears in at least `min_months`
    different months and its amounts vary by no more than `tolerance`
    (10% by default) from their median, which catches subscriptions and bills
    while ignoring a coffee shop you happen to visit often at varying prices.
    """
    s = spending(df)
    if s.empty:
        return pd.DataFrame(columns=["merchant", "category", "typical_amount", "months_seen", "last_charged"])
    s["month"] = s["date"].dt.to_period("M")
    rows = []
    for merchant, group in s.groupby("merchant"):
        if group["month"].nunique() < min_months:
            continue
        median = group["spent"].median()
        if median == 0:
            continue
        if ((group["spent"] - median).abs() / median).max() <= tolerance:
            rows.append({
                "merchant": merchant,
                "category": group["category"].mode().iat[0],
                "typical_amount": round(float(median), 2),
                "months_seen": int(group["month"].nunique()),
                "last_charged": group["date"].max().date(),
            })
    result = pd.DataFrame(rows)
    return result.sort_values("typical_amount", ascending=False).reset_index(drop=True) if not result.empty else result
