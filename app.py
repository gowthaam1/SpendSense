"""SpendSense dashboard. Run with:  streamlit run app.py"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pandas as pd
import altair as alt
import streamlit as st

from spendsense import CATEGORIES
from spendsense.analytics import by_category, by_month, recurring_charges, totals
from spendsense.llm import get_client
from spendsense.parser import StatementFormatError, load_statement
from spendsense.pipeline import DEFAULT_THRESHOLD, apply_corrections, categorize
from spendsense.summary import build_summary_inputs, write_summary

SAMPLE = Path(__file__).parent / "data" / "sample_statement.csv"

st.set_page_config(page_title="SpendSense", page_icon="💸", layout="wide")


def api_key_from_config() -> str | None:
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:  # no secrets.toml
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY")


# ---------- Sidebar ----------
with st.sidebar:
    st.title("💸 SpendSense")
    st.caption("Upload a bank statement CSV. Personal details are masked before anything is sent to the AI.")
    uploaded = st.file_uploader("Bank statement (CSV)", type=["csv"])
    use_sample = st.toggle("Use sample data", value=uploaded is None)

    configured_key = api_key_from_config()
    typed_key = None
    if not configured_key:
        typed_key = st.text_input("Anthropic API key (optional)", type="password",
                                  help="Without a key, rules still work and unknown merchants go to the review queue.")
    client = get_client(configured_key or typed_key)
    st.markdown(f"**AI:** {'🟢 connected' if client else '⚪ off (rules only)'}")

    threshold = st.slider("Review anything below this confidence", 0.0, 1.0, DEFAULT_THRESHOLD, 0.05)

# ---------- Load ----------
if uploaded is not None and not use_sample:
    raw_bytes = uploaded.getvalue()
elif use_sample:
    raw_bytes = SAMPLE.read_bytes()
else:
    st.info("Upload a CSV or turn on sample data to get started.")
    st.stop()

try:
    statement = load_statement(raw_bytes)
except StatementFormatError as err:
    st.error(f"I couldn't read that file. {err}")
    st.stop()

# Only re-run categorization (and pay for API calls) when the file or AI setting changes.
run_key = hashlib.sha256(raw_bytes).hexdigest() + ("-ai" if client else "-rules")
state = st.session_state
if state.get("run_key") != run_key:
    with st.spinner("Categorizing transactions..."):
        state.categorized = categorize(statement, client=client, threshold=threshold, cache={})
    state.run_key = run_key
    state.corrections = {}
    state.pop("summary", None)

df = apply_corrections(state.categorized, state.corrections)
df["needs_review"] = (df["confidence"] < threshold) & (df["source"] != "user")

stats = totals(df)
categories = by_category(df)
monthly = by_month(df)
recurring = recurring_charges(df)

# ---------- Header metrics ----------
st.header("Where your money went")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Spent", f"${stats['total_spent']:,.0f}", help="Excludes transfers between accounts")
c2.metric("Income", f"${stats['total_income']:,.0f}")
c3.metric("Avg per month", f"${stats['avg_monthly_spent']:,.0f}")
c4.metric("To review", stats["needs_review"])

tab_overview, tab_review, tab_recurring, tab_all, tab_how = st.tabs(
    ["Overview", f"Review ({stats['needs_review']})", "Recurring", "All transactions", "How it works"]
)

with tab_overview:
    summary_inputs = build_summary_inputs(stats, categories, monthly, recurring)
    if st.button("✨ Write my summary") or "summary" in state:
        if "summary" not in state:
            with st.spinner("Writing summary..."):
                state.summary = write_summary(summary_inputs, client=client)
        text, source = state.summary
        st.info(text)
        st.caption("Written by Claude from totals only" if source == "ai" else "Template summary (add an API key for an AI-written one)")

    left, right = st.columns(2)
    with left:
        st.subheader("By category")
        if not categories.empty:
            chart = (alt.Chart(categories).mark_bar()
                     .encode(x=alt.X("total:Q", title="Spent ($)"),
                             y=alt.Y("category:N", sort="-x", title=None),
                             tooltip=["category", alt.Tooltip("total:Q", format="$,.2f"), "count"]))
            st.altair_chart(chart, width="stretch")
            st.dataframe(categories.assign(share=lambda d: (d["share"] * 100).round(1).astype(str) + "%"),
                         hide_index=True, width="stretch")
    with right:
        st.subheader("By month")
        if not monthly.empty:
            st.bar_chart(monthly)
            st.line_chart(monthly.sum(axis=1).rename("Total spent"))

with tab_review:
    flagged = df[df["needs_review"]]
    if flagged.empty:
        st.success("Nothing to review. Every transaction was categorized confidently.")
    else:
        st.write("These weren't categorized confidently. Fix any that are wrong; "
                 "a correction applies to every transaction from that merchant.")
        per_merchant = (flagged.groupby("merchant")
                        .agg(category=("category", "first"), confidence=("confidence", "max"),
                             transactions=("amount", "count"), total=("amount", "sum"),
                             example=("description", "first"))
                        .reset_index())
        edited = st.data_editor(
            per_merchant,
            column_config={
                "category": st.column_config.SelectboxColumn("Category", options=CATEGORIES, required=True),
                "confidence": st.column_config.ProgressColumn("AI confidence", min_value=0, max_value=1),
                "total": st.column_config.NumberColumn("Total", format="$%.2f"),
            },
            disabled=["merchant", "confidence", "transactions", "total", "example"],
            hide_index=True, width="stretch", key="review_editor",
        )
        if st.button("Confirm categories", type="primary"):
            for _, row in edited.iterrows():
                state.corrections[row["merchant"]] = row["category"]
            state.pop("summary", None)
            st.rerun()

with tab_recurring:
    if recurring.empty:
        st.write("No recurring charges found yet (needs at least 3 months of data).")
    else:
        st.metric("Recurring per month", f"${recurring['typical_amount'].sum():,.2f}")
        st.dataframe(recurring, hide_index=True, width="stretch",
                     column_config={"typical_amount": st.column_config.NumberColumn("Typical amount", format="$%.2f")})

with tab_all:
    view = df[["date", "merchant", "description", "amount", "category", "confidence", "source"]]
    pick = st.multiselect("Filter by category", CATEGORIES)
    if pick:
        view = view[view["category"].isin(pick)]
    st.dataframe(view, hide_index=True, width="stretch",
                 column_config={"amount": st.column_config.NumberColumn(format="$%.2f"),
                                "date": st.column_config.DateColumn()})
    st.download_button("Download categorized CSV", view.to_csv(index=False), "spendsense_categorized.csv", "text/csv")

with tab_how:
    counts = df["source"].value_counts()
    st.markdown(f"""
**Pipeline:** CSV → clean merchant names → rules → mask personal info → Claude → validate → review queue

| Step | This statement |
|---|---|
| Categorized by rules (free, instant) | {counts.get('rule', 0)} |
| Categorized by Claude | {counts.get('ai', 0)} |
| Fallback (AI off or invalid answer) | {counts.get('fallback', 0)} |
| Corrected by you | {counts.get('user', 0)} |

- **Privacy:** card/account numbers, emails, phone numbers and e-transfer names are masked before any text reaches the model. The summary uses totals only.
- **Structured output:** Claude must return JSON matching a schema (category from a fixed list + confidence 0-1). Invalid answers are rejected, not trusted.
- **Human in the loop:** anything under the confidence threshold goes to the Review tab instead of being guessed.
- **Cost:** rules handle known merchants, and each unknown merchant is sent once, not once per transaction.
""")
