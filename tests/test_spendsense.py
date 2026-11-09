"""Run with:  pytest -q   (no API key or network needed)"""

from types import SimpleNamespace

import pandas as pd
import pytest

from spendsense.analytics import by_category, recurring_charges, totals
from spendsense.cleaning import normalize_merchant
from spendsense.llm import categorize_with_llm, parse_tool_results
from spendsense.parser import StatementFormatError, load_statement
from spendsense.pii import mask_pii
from spendsense.pipeline import apply_corrections, categorize
from spendsense.rules import categorize_by_rules


# ---------- Parser ----------

def test_generic_format():
    df = load_statement(b"Date,Description,Amount\n2026-10-01,NETFLIX,-16.49\n2026-10-02,PAYROLL,400\n")
    assert list(df.columns) == ["date", "description", "amount"]
    assert df["amount"].tolist() == [-16.49, 400.0]


def test_debit_credit_format():
    csv = b"Date,Description,Debit,Credit\n2026-10-01,LOBLAWS,\"$1,045.20\",\n2026-10-02,PAYROLL,,400.00\n"
    df = load_statement(csv)
    assert df["amount"].tolist() == [-1045.20, 400.0]


def test_rbc_format_joins_descriptions_and_drops_account_number():
    csv = (b"Account Type,Account Number,Transaction Date,Cheque Number,Description 1,Description 2,CAD$,USD$\n"
           b"Chequing,01234-5678901,10/01/2026,,SQ *TIM HORTONS,#4421,-4.25,\n")
    df = load_statement(csv)
    assert df.loc[0, "description"] == "SQ *TIM HORTONS #4421"
    assert df.loc[0, "amount"] == -4.25
    assert "Account Number" not in df.columns


def test_bad_file_gives_clear_error():
    with pytest.raises(StatementFormatError):
        load_statement(b"foo,bar\n1,2\n")


# ---------- Cleaning, PII, rules ----------

@pytest.mark.parametrize("raw, expected", [
    ("SQ *TIM HORTONS #4421 TORONTO ON", "Tim Hortons"),
    ("LOBLAWS 1023 MISSISSAUGA ON", "Loblaws"),
    ("PAYPAL *STEAMGAMES", "Steamgames"),
])
def test_normalize_merchant(raw, expected):
    assert normalize_merchant(raw) == expected


@pytest.mark.parametrize("raw, leaked", [
    ("E-TRANSFER SENT John Smith", "John"),
    ("PAYMENT CARD ****1234", "1234"),
    ("ACCT 1234 5678 9012", "5678"),
    ("REFUND jo.smith@gmail.com", "jo.smith"),
    ("CALL 416-555-1234", "555"),
])
def test_pii_is_masked(raw, leaked):
    assert leaked not in mask_pii(raw)


def test_pii_leaves_normal_merchants_alone():
    assert mask_pii("TIM HORTONS") == "TIM HORTONS"


@pytest.mark.parametrize("desc, expected", [
    ("UBER EATS HELP.UBER.COM", "Dining"),       # must beat the plain UBER rule
    ("UBER* TRIP", "Transportation"),
    ("NETFLIX.COM", "Subscriptions"),
    ("METROPOLITAN CAFE", "Dining"),               # METRO must not match METROPOLITAN
    ("SOME UNKNOWN SHOP", None),
])
def test_rules(desc, expected):
    assert categorize_by_rules(desc) == expected


# ---------- AI output validation ----------

def test_invalid_model_output_falls_back():
    raw = [
        {"id": 0, "category": "Dining", "confidence": 0.9},
        {"id": 1, "category": "Snacks", "confidence": 0.9},      # not a real category
        {"id": 2, "category": "Shopping", "confidence": 7},       # confidence out of range
        {"id": 99, "category": "Dining", "confidence": 0.9},      # id we never asked about
    ]
    parsed = parse_tool_results(raw, expected_ids={0, 1, 2, 3})
    assert parsed[0]["category"] == "Dining" and parsed[0]["source"] == "ai"
    for i in (1, 2, 3):  # 3 was missing entirely
        assert parsed[i] == {"category": "Other", "confidence": 0.0, "source": "fallback"}
    assert 99 not in parsed


def test_no_client_means_fallback_not_crash():
    assert categorize_with_llm([(0, "MYSTERY")], client=None)[0]["source"] == "fallback"


class FakeClient:
    """Stands in for the Anthropic client and records what was sent."""

    def __init__(self, category="Dining", confidence=0.9, fail=False):
        self.sent, self.calls = [], 0
        self.category, self.confidence, self.fail = category, confidence, fail
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("API down")
        prompt = kwargs["messages"][0]["content"]
        self.sent.append(prompt)
        lines = [l for l in prompt.splitlines()[1:] if ":" in l]
        ids = [int(l.split(":", 1)[0]) for l in lines]
        results = [{"id": i, "category": self.category, "confidence": self.confidence} for i in ids]
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"results": results})])


def sample_df():
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]),
        "description": ["NETFLIX.COM", "SQ *KAPOW NOODLE BAR", "SQ *KAPOW NOODLE BAR",
                        "MYSTERY SHOP ref 4519 0011 2233"],
        "amount": [-16.49, -20.0, -25.0, -30.0],
    })


def test_pipeline_rules_then_ai_with_masking_and_cache():
    client = FakeClient()
    out = categorize(sample_df(), client=client)
    assert out.loc[0, "source"] == "rule"
    assert out.loc[1, "source"] == "ai" and out.loc[1, "category"] == "Dining"
    assert client.calls == 1                       # one batched call
    sent = "\n".join(client.sent)
    assert sent.count("KAPOW") == 1                # repeat merchant sent once
    assert "4519" not in sent and "[NUMBER]" in sent  # PII masked before sending
    assert "NETFLIX" not in sent                   # rule matches never sent


def test_low_confidence_goes_to_review():
    out = categorize(sample_df(), client=FakeClient(confidence=0.4), threshold=0.7)
    assert out.loc[1, "needs_review"] and not out.loc[0, "needs_review"]


def test_api_failure_does_not_crash():
    out = categorize(sample_df(), client=FakeClient(fail=True))
    assert (out.loc[1:, "source"] == "fallback").all()
    assert out.loc[1:, "needs_review"].all()


def test_user_correction_applies_to_all_rows_for_merchant():
    out = categorize(sample_df(), client=None)
    merchant = out.loc[1, "merchant"]
    fixed = apply_corrections(out, {merchant: "Dining"})
    assert (fixed.loc[fixed["merchant"] == merchant, "category"] == "Dining").all()
    assert not fixed.loc[fixed["merchant"] == merchant, "needs_review"].any()


# ---------- Analytics ----------

def test_recurring_detection_and_totals():
    dates = pd.to_datetime(["2026-08-03", "2026-09-03", "2026-10-03",
                            "2026-08-05", "2026-09-09", "2026-10-21",
                            "2026-08-15", "2026-09-15"])
    df = pd.DataFrame({
        "date": dates,
        "merchant": ["Netflix"] * 3 + ["Tim Hortons"] * 3 + ["Payroll"] * 2,
        "category": ["Subscriptions"] * 3 + ["Dining"] * 3 + ["Income"] * 2,
        "amount": [-16.49] * 3 + [-2.5, -9.0, -5.0] + [400, 400],
        "needs_review": [False] * 8,
    })
    recurring = recurring_charges(df)
    assert recurring["merchant"].tolist() == ["Netflix"]   # coffee varies too much
    t = totals(df)
    assert t["total_income"] == 800 and t["months"] == 3
    assert by_category(df).iloc[0]["category"] == "Subscriptions"
