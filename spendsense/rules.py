"""Rules-first categorization.

Most transactions are boring and obvious: Netflix is a subscription, Loblaws
is groceries. Sending those to an AI model costs money, adds latency and
introduces randomness for no benefit. Rules handle the easy majority
deterministically; only the leftovers go to the model.
"""

from __future__ import annotations

import re

# (category, keywords). Checked in order, so put more specific rules first
# (e.g. "UBER EATS" must be checked before "UBER").
RULES: list[tuple[str, list[str]]] = [
    ("Income", ["PAYROLL", "DIRECT DEPOSIT", "SALARY", "PAY DEPOSIT", "CANADA FED", "GST CREDIT", "OSAP"]),
    ("Fees", ["SERVICE CHARGE", "MONTHLY FEE", "NSF FEE", "OVERDRAFT", "INTEREST CHARGE", "ANNUAL FEE", "ATM FEE"]),
    ("Transfers", ["E-TRANSFER", "ETRANSFER", "INTERAC E-TRANSFER", "TRANSFER TO", "TRANSFER FROM", "WIRE"]),
    ("Dining", ["UBER EATS", "UBEREATS", "DOORDASH", "SKIP THE DISHES", "SKIPTHEDISHES", "TIM HORTONS",
                "STARBUCKS", "MCDONALD", "A&W", "WENDY", "SUBWAY", "CHIPOTLE", "POPEYES", "PIZZA",
                "MARY BROWN", "OSMOW", "BURGER", "SUSHI", "RESTAURANT", "CAFE", "COFFEE"]),
    ("Transportation", ["UBER", "LYFT", "PRESTO", "GO TRANSIT", "TTC", "MIWAY", "PETRO-CANADA",
                        "PETRO CANADA", "SHELL", "ESSO", "PIONEER", "HUSKY", "IMPARK", "GREEN P",
                        "407 ETR", "VIA RAIL"]),
    ("Groceries", ["LOBLAWS", "NO FRILLS", "NOFRILLS", "SOBEYS", "METRO", "FRESHCO", "FOOD BASICS",
                   "COSTCO", "T&T", "FARM BOY", "WHOLE FOODS", "FORTINOS", "ZEHRS", "REAL CANADIAN"]),
    ("Subscriptions", ["NETFLIX", "SPOTIFY", "DISNEY PLUS", "DISNEYPLUS", "CRAVE", "APPLE.COM/BILL",
                       "AMAZON PRIME", "PRIME VIDEO", "YOUTUBE PREMIUM", "CHATGPT", "OPENAI",
                       "ANTHROPIC", "CLAUDE.AI", "ICLOUD", "GOOGLE STORAGE", "ADOBE", "GOODLIFE"]),
    ("Bills & Utilities", ["ROGERS", "BELL CANADA", "TELUS", "FIDO", "KOODO", "VIRGIN PLUS",
                           "FREEDOM MOBILE", "PUBLIC MOBILE", "HYDRO", "ENBRIDGE", "ALECTRA",
                           "INSURANCE"]),
    ("Rent & Housing", ["RENT", "PROPERTY MGMT", "PROPERTY MANAGEMENT", "MORTGAGE"]),
    ("Health", ["SHOPPERS DRUG", "REXALL", "PHARMA", "DENTAL", "CLINIC", "PHYSIO", "OPTOMETR"]),
    ("Education", ["WESTERN UNIV", "UWO", "BOOKSTORE", "COURSERA", "UDEMY", "PEARSON"]),
    ("Entertainment", ["CINEPLEX", "TICKETMASTER", "STEAM", "PLAYSTATION", "XBOX", "NINTENDO", "LANDMARK CINEMA"]),
    ("Shopping", ["AMAZON", "AMZN", "WALMART", "BEST BUY", "CANADIAN TIRE", "WINNERS", "HOMESENSE",
                  "MARSHALLS", "IKEA", "SPORT CHEK", "HUDSON'S BAY", "UNIQLO", "ZARA", "H&M", "DOLLARAMA",
                  "APPLE STORE", "SEPHORA"]),
]

# Pre-compile: keyword must start at a word boundary so "METRO" doesn't match "METROPOLITAN".
_COMPILED = [
    (category, [re.compile(r"(?<![A-Z0-9])" + re.escape(k)) for k in keywords])
    for category, keywords in RULES
]


def categorize_by_rules(description: str) -> str | None:
    """Return a category if a rule matches, otherwise None (send to the AI)."""
    text = description.upper()
    for category, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            return category
    return None
