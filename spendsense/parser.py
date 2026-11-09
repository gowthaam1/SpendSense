"""Load a bank-statement CSV into one standard shape.

Every bank exports CSVs differently. Instead of writing one parser per bank,
we look for columns by common names and convert them into:

    date (datetime) | description (str) | amount (float, negative = money out)

Supported out of the box:
  * Generic exports:  Date, Description, Amount
  * Debit/Credit:     Date, Description, Debit, Credit  (e.g. TD, Scotiabank)
  * RBC exports:      Transaction Date, Description 1, Description 2, CAD$
"""

from __future__ import annotations

import io

import pandas as pd

DATE_COLS = ["date", "transaction date", "posted date", "posting date", "trans date"]
DESC_COLS = ["description", "description 1", "merchant", "details",
             "transaction details", "payee", "name"]
EXTRA_DESC_COLS = ["description 2"]
AMOUNT_COLS = ["amount", "cad$", "amount (cad)", "transaction amount"]
DEBIT_COLS = ["debit", "withdrawal", "withdrawals", "money out"]
CREDIT_COLS = ["credit", "deposit", "deposits", "money in"]


class StatementFormatError(ValueError):
    """Raised when we can't find the columns we need."""


def _find(columns: dict[str, str], candidates: list[str]) -> str | None:
    """Return the original column name matching the first candidate found."""
    for name in candidates:
        if name in columns:
            return columns[name]
    return None


def _to_number(series: pd.Series) -> pd.Series:
    """Turn '$1,234.50', '(12.00)' or '' into floats."""
    cleaned = (
        series.astype(str)
        .str.replace(r"[$,\s]", "", regex=True)
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)  # (12.00) -> -12.00
        .replace({"": None, "nan": None, "None": None})
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)


def load_statement(source) -> pd.DataFrame:
    """Read a CSV (path, file object, or raw bytes) into the standard shape."""
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    raw = pd.read_csv(source, dtype=str, skipinitialspace=True)
    raw.columns = [c.strip() for c in raw.columns]
    columns = {c.lower(): c for c in raw.columns}

    date_col = _find(columns, DATE_COLS)
    desc_col = _find(columns, DESC_COLS)
    if not date_col or not desc_col:
        raise StatementFormatError(
            f"Couldn't find a date and description column. Columns found: {list(raw.columns)}"
        )

    description = raw[desc_col].fillna("").astype(str)
    extra = _find(columns, EXTRA_DESC_COLS)
    if extra:
        description = (description + " " + raw[extra].fillna("").astype(str)).str.strip()

    amount_col = _find(columns, AMOUNT_COLS)
    debit_col = _find(columns, DEBIT_COLS)
    credit_col = _find(columns, CREDIT_COLS)
    if amount_col:
        amount = _to_number(raw[amount_col])
    elif debit_col or credit_col:
        debit = _to_number(raw[debit_col]).abs() if debit_col else 0.0
        credit = _to_number(raw[credit_col]).abs() if credit_col else 0.0
        amount = credit - debit
    else:
        raise StatementFormatError(
            f"Couldn't find an amount column (or debit/credit columns). Columns found: {list(raw.columns)}"
        )

    df = pd.DataFrame({
        "date": pd.to_datetime(raw[date_col], errors="coerce"),
        "description": description,
        "amount": amount,
    })
    # Drop rows we can't use (blank lines, totals rows, bad dates).
    df = df.dropna(subset=["date"])
    df = df[df["description"].str.strip() != ""]
    return df.sort_values("date").reset_index(drop=True)
