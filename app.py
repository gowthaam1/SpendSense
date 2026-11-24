"""SpendSense dashboard. Run with:  streamlit run app.py"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from spendsense import CATEGORIES
from spendsense.analytics import by_category, by_month, recurring_charges, totals
from spendsense.export import to_safe_csv
from spendsense.llm import get_client
from spendsense.parser import ColumnMap, StatementFormatError, load_statement
from spendsense.pipeline import DEFAULT_THRESHOLD, apply_corrections, categorize
from spendsense.summary import build_summary_inputs, write_summary

SAMPLE = Path(__file__).parent / "data" / "sample_statement.csv"

st.set_page_config(page_title="SpendSense", layout="wide")

ACCENT = "#1F5C46"
MUTED = "#B9C7BF"


def api_key_from_config() -> str | None:
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:  # no secrets.toml
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY")


# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("### SpendSense")
    st.caption("Upload a bank statement CSV. Personal details are masked before anything is sent to the AI.")
    uploaded = st.file_uploader("Bank statement (CSV)", type=["csv"])
    with st.expander("File options"):
        flip_sign = st.toggle("Credit card statement", help="Turn on if purchases show as positive numbers.")
        dayfirst = st.toggle("Dates are day/month", help="Read 03/04/2026 as 3 April instead of March 4.")

    configured_key = api_key_from_config()
    typed_key = None
    if not configured_key:
        typed_key = st.text_input("Anthropic API key (optional)", type="password",
                                  help="Without a key, rules still work and unknown merchants go to the review queue.")
    client = get_client(configured_key or typed_key)
    st.caption(f"AI categorization: {'on' if client else 'off, using rules only'}")


    st.divider()
    st.caption("Nothing is saved. Your file stays in memory for this session only and is cleared when you close "
               "the tab. On the public demo, please use the sample data.")

threshold = DEFAULT_THRESHOLD

# ---------- Load ----------
using_sample = uploaded is None
raw_bytes = SAMPLE.read_bytes() if using_sample else uploaded.getvalue()

state = st.session_state
file_hash = hashlib.sha256(raw_bytes).hexdigest()
mapping = state.get("mapping") if state.get("mapping_file") == file_hash else None

try:
    statement = load_statement(raw_bytes, mapping=mapping, flip_sign=flip_sign, dayfirst=dayfirst)
except StatementFormatError as err:
    if not err.columns:
        st.error(f"I couldn't read that file. {err}")
        st.stop()
    st.title("Match your columns")
    st.write(f"{err} Pick which column is which and I'll take it from there.")
    st.dataframe(err.preview, hide_index=True, width="stretch")
    none = "(none)"
    with st.form("column_map"):
        c1, c2 = st.columns(2)
        date_col = c1.selectbox("Date", err.columns)
        desc_col = c2.selectbox("Description", err.columns, index=min(1, len(err.columns) - 1))
        c3, c4, c5 = st.columns(3)
        amount_col = c3.selectbox("Amount (one column)", [none] + err.columns)
        debit_col = c4.selectbox("or Debit / money out", [none] + err.columns)
        credit_col = c5.selectbox("and Credit / money in", [none] + err.columns)
        if st.form_submit_button("Use these columns", type="primary"):
            amount_col, debit_col, credit_col = (None if v == none else v
                                                 for v in (amount_col, debit_col, credit_col))
            if not (amount_col or debit_col or credit_col):
                st.error("Choose an amount column, or debit and credit columns.")
            else:
                state.mapping = ColumnMap(date_col, desc_col, amount_col, debit_col, credit_col)
                state.mapping_file = file_hash
                st.rerun()
    st.stop()

# Only re-run categorization (and pay for API calls) when the file or a setting changes.
run_key = f"{file_hash}-{'ai' if client else 'rules'}-{flip_sign}-{dayfirst}-{mapping}"
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
start, end = df["date"].min(), df["date"].max()
st.title("Spending overview")
st.caption(f"{start:%b %-d, %Y} – {end:%b %-d, %Y} · {stats['transactions']} transactions"
           + (" · sample data (upload your own CSV in the sidebar)" if using_sample else ""))
c1, c2, c3, c4 = st.columns(4)
c1.metric("Spent", f"${stats['total_spent']:,.0f}", help="Excludes transfers between accounts")
c2.metric("Income", f"${stats['total_income']:,.0f}")
net = stats["net"]
c3.metric("Net", f"{'-' if net < 0 else ''}${abs(net):,.0f}", help="Income minus spending")
c4.metric("Avg spent / month", f"${stats['avg_monthly_spent']:,.0f}")

tab_overview, tab_review, tab_recurring, tab_all, tab_how = st.tabs(
    ["Overview", f"Review ({stats['needs_review']})", "Recurring", "All transactions", "How it works"]
)

with tab_overview:
    summary_inputs = build_summary_inputs(stats, categories, monthly, recurring)
    if st.button("Generate summary") or "summary" in state:
        if "summary" not in state:
            with st.spinner("Writing summary..."):
                state.summary = write_summary(summary_inputs, client=client)
        text, source = state.summary
        with st.container(border=True):
            st.write(text)
            st.caption("Written by Claude from totals only" if source == "ai" else "Template summary (add an API key for an AI-written one)")

    left, right = st.columns(2)
    with left:
        st.subheader("By category")
        if not categories.empty:
            chart = (alt.Chart(categories).mark_bar(color=ACCENT, cornerRadiusEnd=2)
                     .encode(x=alt.X("total:Q", title="Spent ($)"),
                             y=alt.Y("category:N", sort="-x", title=None, axis=alt.Axis(ticks=False, domain=False)),
                             tooltip=["category", alt.Tooltip("total:Q", format="$,.2f"), "count"]))
            st.altair_chart(chart.configure_view(stroke=None).configure_axis(grid=False, labelColor="#555"),
                            width="stretch")
            st.dataframe(categories, hide_index=True, width="stretch",
                         column_config={"category": "Category",
                                        "total": st.column_config.NumberColumn("Total", format="$%.2f"),
                                        "count": "Transactions",
                                        "share": st.column_config.ProgressColumn("Share", format="percent",
                                                                                 min_value=0, max_value=1)})
    with right:
        st.subheader("By month")
        if not monthly.empty:
            month_totals = monthly.sum(axis=1).rename("total").reset_index()
            month_totals["label"] = pd.to_datetime(month_totals["month"]).dt.strftime("%b %Y")
            latest = month_totals["month"].max()
            bars = (alt.Chart(month_totals)
                    .mark_bar(cornerRadiusEnd=2, size=48)
                    .encode(x=alt.X("label:N", sort=None, title=None, axis=alt.Axis(labelAngle=0, ticks=False)),
                            y=alt.Y("total:Q", title="Spent ($)"),
                            color=alt.condition(alt.datum.month == latest, alt.value(ACCENT), alt.value(MUTED)),
                            tooltip=["label", alt.Tooltip("total:Q", format="$,.2f")]))
            text = bars.mark_text(dy=-8, color="#333").encode(text=alt.Text("total:Q", format="$,.0f"))
            st.altair_chart((bars + text).configure_view(stroke=None).configure_axis(grid=False, labelColor="#555"),
                            width="stretch")
            if len(month_totals) > 1:
                prev, last = month_totals["total"].iloc[-2], month_totals["total"].iloc[-1]
                change = (last - prev) / prev * 100 if prev else 0
                st.caption(f"{month_totals['label'].iloc[-1]} spending was {abs(change):.0f}% "
                           f"{'lower' if change < 0 else 'higher'} than the month before.")

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
    st.download_button("Download categorized CSV", to_safe_csv(view), "spendsense_categorized.csv", "text/csv")

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

- **Privacy:** card/account numbers, emails, phone numbers, street addresses, postal codes and e-transfer names are masked before any text reaches the model. The summary uses totals only, and nothing is written to disk.
- **Structured output:** Claude must return JSON matching a schema (category from a fixed list + confidence 0-1). Invalid answers are rejected, not trusted.
- **Human in the loop:** anything Claude is less than {DEFAULT_THRESHOLD:.0%} confident about goes to the Review tab instead of being guessed.
- **Cost:** rules handle known merchants, and each unknown merchant is sent once, not once per transaction.
""")
