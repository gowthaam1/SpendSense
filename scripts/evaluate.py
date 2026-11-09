"""Measure how accurate the categorizer is on a hand-labeled set.

    python scripts/evaluate.py                       # rules only
    ANTHROPIC_API_KEY=sk-... python scripts/evaluate.py  # rules + Claude

For a number you can honestly put on a resume, label ~100 of YOUR OWN real
transactions by hand in data/private/my_labeled.csv (same two columns as
data/eval_labeled.csv) and pass that path. data/private/ is git-ignored.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spendsense.llm import get_client  # noqa: E402
from spendsense.pipeline import categorize  # noqa: E402

DEFAULT_EVAL = Path(__file__).resolve().parent.parent / "data" / "eval_labeled.csv"
# Pass your own labeled file to keep real transactions out of the public repo:
#   python scripts/evaluate.py data/private/my_labeled.csv
EVAL = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EVAL


def main() -> None:
    labeled = pd.read_csv(EVAL)
    # Amount sign matters for the "money in" fallback, so infer it from the label.
    df = pd.DataFrame({
        "date": pd.Timestamp("2026-10-01"),
        "description": labeled["description"],
        "amount": labeled["true_category"].map(lambda c: 100.0 if c == "Income" else -10.0),
    })
    client = get_client()
    result = categorize(df, client=client)
    result["true_category"] = labeled["true_category"]
    result["correct"] = result["category"] == result["true_category"]

    mode = "rules + Claude" if client else "rules only (no API key)"
    print(f"\nSpendSense evaluation  [{mode}]  n={len(result)}\n")
    print(f"Overall accuracy:        {result['correct'].mean():.1%}")
    by_rule = result[result["source"] == "rule"]
    print(f"Handled by rules:        {len(by_rule)}/{len(result)} ({len(by_rule)/len(result):.0%}), "
          f"accuracy {by_rule['correct'].mean():.1%}")
    by_ai = result[result["source"] == "ai"]
    if len(by_ai):
        print(f"Handled by Claude:       {len(by_ai)}/{len(result)}, accuracy {by_ai['correct'].mean():.1%}")
    flagged = result[result["needs_review"]]
    print(f"Sent to review queue:    {len(flagged)}")
    confident = result[~result["needs_review"]]
    if len(confident):
        print(f"Accuracy when confident: {confident['correct'].mean():.1%}  (what the user sees without reviewing)")

    print("\nPer category:")
    per = result.groupby("true_category")["correct"].agg(["mean", "count"]).sort_values("mean")
    for cat, row in per.iterrows():
        print(f"  {cat:<18} {row['mean']:.0%}  (n={int(row['count'])})")

    wrong = result[~result["correct"] & (result["source"] != "fallback")]
    if len(wrong):
        print("\nConfident mistakes worth a look:")
        for _, r in wrong.iterrows():
            print(f"  {r['description']:<42} predicted {r['category']:<16} actual {r['true_category']}")


if __name__ == "__main__":
    main()
