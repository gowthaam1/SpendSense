"""Turn messy bank descriptions into readable merchant names.

    'SQ *TIM HORTONS #4421 TORONTO ON'  ->  'Tim Hortons'
    'POS PURCHASE NETFLIX.COM 866-579'  ->  'Netflix.com'
"""

from __future__ import annotations

import re

# Payment-processor and point-of-sale prefixes that hide the real merchant.
PREFIXES = [
    r"SQ \*", r"TST\* ?", r"PAYPAL \*", r"PP\*", r"SP \*", r"DD \*",
    r"POS PURCHASE", r"POS", r"PURCHASE", r"INTERAC PURCHASE",
    r"DEBIT CARD PURCHASE", r"VISA DEBIT PURCHASE", r"CONTACTLESS",
    r"IDP PURCHASE", r"MISC PAYMENT", r"BILL PAYMENT", r"PAYMENT -",
]
PREFIX_RE = re.compile(r"^(?:" + "|".join(PREFIXES) + r")\s*", re.IGNORECASE)

PROVINCES = r"(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)"
TRAILING_LOCATION_RE = re.compile(r"\s+[A-Z][A-Za-z.\- ]{1,20}\s+" + PROVINCES + r"$")
STORE_NUMBER_RE = re.compile(r"\s*#\s*\d+")
LONG_DIGITS_RE = re.compile(r"\b[\d\-]{4,}\b")
# Reference codes that mix letters and digits, e.g. "P2F41A9C" or "*2K4L9".
REF_CODE_RE = re.compile(r"[*\s](?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5,}\b")
EXTRA_SPACE_RE = re.compile(r"\s{2,}")


def normalize_merchant(description: str) -> str:
    text = description.strip().upper()
    # Strip prefixes repeatedly ("POS PURCHASE SQ *..." has two).
    previous = None
    while previous != text:
        previous = text
        text = PREFIX_RE.sub("", text).strip()
    text = TRAILING_LOCATION_RE.sub("", text)
    text = STORE_NUMBER_RE.sub("", text)
    text = REF_CODE_RE.sub("", text)
    text = LONG_DIGITS_RE.sub("", text)
    text = EXTRA_SPACE_RE.sub(" ", text).strip(" -*")
    return text.title() if text else description.strip()
