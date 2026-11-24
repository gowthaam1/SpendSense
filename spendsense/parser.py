"""Load a bank-statement CSV into one standard shape.

Every bank exports CSVs differently. Instead of writing one parser per bank,
we look for columns by common names and convert them into:

    date (datetime) | description (str) | amount (float, negative = money out)

Handled automatically:
  * Generic exports:   Date, Description, Amount
  * Debit/Credit:      Date, Description, Debit, Credit  (e.g. TD, Scotiabank)
  * RBC exports:       Transaction Date, Description 1, Description 2, CAD$
  * Preamble rows:     account info above the real header row is skipped
  * No header row:     columns are inferred from their contents (e.g. CIBC-style
                       date, description, debit, credit)

When none of that works, StatementFormatError carries the raw columns and a
preview so the app can ask the user to map the columns by hand.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import pandas as pd

DATE_COLS = ["date", "transaction date", "posted date", "posting date", "trans date",
             "date de transaction", "date de l'opération"]
DESC_COLS = ["description", "description 1", "merchant", "details",
             "transaction details", "payee", "name", "libellé"]
EXTRA_DESC_COLS = ["description 2"]
AMOUNT_COLS = ["amount", "cad$", "amount (cad)", "transaction amount", "montant"]
DEBIT_COLS = ["debit", "withdrawal", "withdrawals", "money out", "débit", "retrait"]
CREDIT_COLS = ["credit", "deposit", "deposits", "money in", "crédit", "dépôt"]

MAX_PREAMBLE_ROWS = 15
MAX_ROWS = 20_000


class StatementFormatError(ValueError):
    """Raised when we can't work out the columns. Carries what we did find."""

    def __init__(self, message: str, columns: list[str] | None = None,
                 preview: pd.DataFrame | None = None):
        super().__init__(message)
        self.columns = columns or []
        self.preview = preview


@dataclass
class ColumnMap:
    """Which raw column holds what. Use either `amount` or `debit`/`credit`."""
    date: str
    description: str
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None


def _find(columns: dict[str, str], candidates: list[str]) -> str | None:
    """Return the original column name matching the first candidate found."""
    for name in candidates:
        if name in columns:
            return columns[name]
    return None


def _to_number(series: pd.Series) -> pd.Series:
    """Turn '$1,234.50', '(12.00)', '-12.00 CR' or '' into floats."""
    cleaned = (
        series.astype(str)
        .str.replace(r"[$,\s]", "", regex=True)
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)  # (12.00) -> -12.00
        .replace({"": None, "nan": None, "None": None})
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)


def _parse_dates(series: pd.Series, dayfirst: bool) -> pd.Series:
    """Parse dates, trying ISO first so 2026-10-03 is never read day-first."""
    s = series.astype(str).str.strip()
    iso = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    other = pd.to_datetime(s, errors="coerce", dayfirst=dayfirst, format="mixed")
    return iso.fillna(other)


def _looks_like_dates(series: pd.Series) -> float:
    parsed = pd.to_datetime(series.astype(str).str.strip(), errors="coerce", format="mixed")
    return parsed.notna().mean()


def _looks_numeric(series: pd.Series) -> float:
    s = series.dropna().astype(str).str.strip()
    s = s[s != ""]
    if s.empty:
        return 0.0
    cleaned = s.str.replace(r"[$,\s()]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce").notna().mean()


def _read_raw(source) -> pd.DataFrame:
    """Read every row as text, tolerating ragged rows (preambles have fewer fields)."""
    if isinstance(source, (str, bytes, bytearray)) is False and hasattr(source, "read"):
        source = source.read()
    if isinstance(source, str):
        with open(source, "rb") as fh:
            source = fh.read()
    text = bytes(source).decode("utf-8-sig", errors="replace")
    try:
        rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)][:MAX_ROWS]
    except csv.Error as err:
        raise StatementFormatError(f"That doesn't look like a CSV file ({err}).") from err
    if not rows:
        return pd.DataFrame()
    width = max(len(r) for r in rows)
    padded = [[c.strip() or None for c in r] + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(padded, dtype=object)


def _with_header(raw: pd.DataFrame) -> pd.DataFrame | None:
    """Find the real header row (skipping account-info rows above it)."""
    for i in range(min(MAX_PREAMBLE_ROWS, len(raw))):
        row = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
        if any(c in row for c in DATE_COLS) and any(c in row for c in DESC_COLS):
            body = raw.iloc[i + 1:].copy()
            body.columns = [str(v).strip() if pd.notna(v) else f"column {j + 1}"
                            for j, v in enumerate(raw.iloc[i].tolist())]
            return body.reset_index(drop=True)
    return None


def _infer_headerless(raw: pd.DataFrame) -> ColumnMap | None:
    """Guess columns from contents for exports with no header row."""
    raw = raw.copy()
    raw.columns = [f"column {j + 1}" for j in range(raw.shape[1])]
    sample = raw.head(50)
    date_col = next((c for c in raw.columns if _looks_like_dates(sample[c]) > 0.8), None)
    if not date_col:
        return None
    others = [c for c in raw.columns if c != date_col]
    numeric = [c for c in others if _looks_numeric(sample[c]) > 0.9
               and sample[c].dropna().astype(str).str.contains(r"\.\d{2}").any()]
    text = [c for c in others if c not in numeric]
    if not text or not numeric:
        return None
    # The description is the column with the most letters (not a card number like 4500****1234).
    desc_col = max(text, key=lambda c: sample[c].dropna().astype(str).str.count(r"[A-Za-z]").mean())
    if len(numeric) >= 2:
        return ColumnMap(date=date_col, description=desc_col, debit=numeric[0], credit=numeric[1])
    return ColumnMap(date=date_col, description=desc_col, amount=numeric[0])


def _auto_map(body: pd.DataFrame) -> tuple[ColumnMap | None, str | None]:
    columns = {c.lower(): c for c in body.columns}
    date_col, desc_col = _find(columns, DATE_COLS), _find(columns, DESC_COLS)
    amount = _find(columns, AMOUNT_COLS)
    debit, credit = _find(columns, DEBIT_COLS), _find(columns, CREDIT_COLS)
    if not (date_col and desc_col and (amount or debit or credit)):
        return None, None
    return ColumnMap(date_col, desc_col, amount, debit, credit), _find(columns, EXTRA_DESC_COLS)


def read_columns(source) -> tuple[pd.DataFrame, ColumnMap | None]:
    """Return the table (with best-guess column names) and an automatic mapping, if any."""
    raw = _read_raw(source)
    if raw.empty:
        raise StatementFormatError("The file is empty.")
    body = _with_header(raw)
    if body is not None:
        mapping, _ = _auto_map(body)
        return body, mapping
    guess = _infer_headerless(raw)
    if guess is None and _looks_like_dates(raw.iloc[[0]].T[0]) == 0 and _looks_numeric(raw.iloc[0]) < 0.5:
        # Unrecognised but real header row: keep its names so the user can map them.
        body = raw.iloc[1:].copy()
        body.columns = [str(v) if v is not None else f"column {j + 1}" for j, v in enumerate(raw.iloc[0])]
        return body.reset_index(drop=True), None
    raw.columns = [f"column {j + 1}" for j in range(raw.shape[1])]
    return raw, guess


def load_statement(source, mapping: ColumnMap | None = None, flip_sign: bool = False,
                   dayfirst: bool = False) -> pd.DataFrame:
    """Read a CSV (path, file object, or raw bytes) into the standard shape.

    mapping:   override automatic column detection (from the app's mapping form)
    flip_sign: for credit-card exports where purchases are positive numbers
    dayfirst:  read ambiguous dates like 03/04/2026 as 3 April
    """
    body, auto = read_columns(source)
    extra = None
    if mapping is None:
        mapping = auto
        if mapping is not None:
            _, extra = _auto_map(body)
    if mapping is None:
        raise StatementFormatError(
            "Couldn't work out which columns hold the date, description and amount.",
            columns=list(body.columns), preview=body.head(5),
        )
    for col in [mapping.date, mapping.description, mapping.amount, mapping.debit, mapping.credit]:
        if col is not None and col not in body.columns:
            raise StatementFormatError(f"Column '{col}' isn't in this file.",
                                       columns=list(body.columns), preview=body.head(5))

    description = body[mapping.description].fillna("").astype(str)
    if extra and extra in body.columns:
        description = (description + " " + body[extra].fillna("").astype(str)).str.strip()

    if mapping.amount:
        amount = _to_number(body[mapping.amount])
    elif mapping.debit or mapping.credit:
        debit = _to_number(body[mapping.debit]).abs() if mapping.debit else 0.0
        credit = _to_number(body[mapping.credit]).abs() if mapping.credit else 0.0
        amount = credit - debit
    else:
        raise StatementFormatError("Pick an amount column, or debit and credit columns.",
                                   columns=list(body.columns), preview=body.head(5))
    if flip_sign:
        amount = -amount

    df = pd.DataFrame({
        "date": _parse_dates(body[mapping.date], dayfirst),
        "description": description.str.slice(0, 200),
        "amount": amount,
    })
    # Drop rows we can't use (blank lines, totals rows, bad dates).
    df = df.dropna(subset=["date"])
    df = df[df["description"].str.strip() != ""]
    if df.empty:
        raise StatementFormatError("No transactions found. Check the date column.",
                                   columns=list(body.columns), preview=body.head(5))
    return df.sort_values("date").reset_index(drop=True)
