"""Remove personal information before anything leaves your machine.

Only masked text is ever sent to the AI model. This is the same principle a
bank follows: the model doesn't need someone's card number or the name of the
friend they e-transferred to in order to categorize a transaction.
"""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
# Card / account / reference numbers: 6+ digits, optionally with spaces or dashes.
ACCOUNT_RE = re.compile(r"\b\d(?:[\s-]?\d){5,}\b")
# Masked card numbers like ****1234 or XXXX1234.
MASKED_CARD_RE = re.compile(r"[*Xx]{4,}\s?\d{2,4}")
# Street addresses like "1155 RICHMOND ST" or "45 KING STREET W".
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z0-9'.-]+\s+){1,4}"
    r"(?:ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|BOULEVARD|DR|DRIVE|CRES|CRESCENT|CRT|CT|COURT|"
    r"WAY|LANE|LN|PL|PLACE|PKWY|HWY|TERR|TERRACE|CIRCLE|CIR|TRAIL|TRL|SQ)\b\.?"
    r"(?:\s+(?:N|S|E|W|NORTH|SOUTH|EAST|WEST))?\b",
    re.IGNORECASE,
)
# Canadian postal codes like L5B 1M2.
POSTAL_RE = re.compile(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b", re.IGNORECASE)
# A person's name after an e-transfer / transfer keyword.
TRANSFER_NAME_RE = re.compile(
    r"((?:E-?TRANSFER|INTERAC E-?TRANSFER|ETRANSFER|EMT|TRANSFER)"
    r"(?:\s+(?:SENT|RECEIVED|TO|FROM|AUTODEPOSIT))*\s+)"
    r"([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,3})",
    re.IGNORECASE,
)


def mask_pii(text: str) -> str:
    masked = EMAIL_RE.sub("[EMAIL]", text)
    masked = ADDRESS_RE.sub("[ADDRESS]", masked)
    masked = POSTAL_RE.sub("[POSTAL]", masked)
    masked = MASKED_CARD_RE.sub("[CARD]", masked)
    masked = PHONE_RE.sub("[PHONE]", masked)
    masked = ACCOUNT_RE.sub("[NUMBER]", masked)
    masked = TRANSFER_NAME_RE.sub(lambda m: m.group(1) + "[PERSON]", masked)
    return masked


def contains_pii(text: str) -> bool:
    return mask_pii(text) != text
