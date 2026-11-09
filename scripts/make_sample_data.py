"""Generate a fake but realistic 3-month student bank statement.

Every name and number here is invented. Run:  python scripts/make_sample_data.py
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(3000)
OUT = Path(__file__).resolve().parent.parent / "data" / "sample_statement.csv"

MONTHS = [(2026, 8), (2026, 9), (2026, 10)]

# (description, amount_low, amount_high, times_per_month)
VARIABLE = [
    ("SQ *TIM HORTONS #4421 TORONTO ON", 2.5, 9, 6),
    ("STARBUCKS 08812 MISSISSAUGA ON", 5, 9, 2),
    ("UBER EATS HELP.UBER.COM", 18, 42, 3),
    ("LOBLAWS 1023 MISSISSAUGA ON", 35, 120, 2),
    ("NO FRILLS #3321 LONDON ON", 25, 80, 2),
    ("PRESTO FARE/LOAD", 20, 40, 2),
    ("UBER* TRIP HELP.UBER.COM", 11, 28, 2),
    ("PETRO-CANADA 4410", 45, 70, 1),
    ("AMZN MKTP CA*2K4L9", 15, 90, 2),
    ("DOLLARAMA #1187", 4, 20, 1),
    # Merchants the rules don't know: these go to the AI.
    ("TST* THE GROVE KITCHEN LONDON ON", 22, 48, 1),
    ("SQ *KAPOW NOODLE BAR", 16, 30, 1),
    ("PAYPAL *STEAMGAMES", 10, 40, 1),
    ("THE SPOKE TAVERN", 15, 35, 1),
    ("WESTERN UNIV BOOKSTORE", 30, 140, 1),
]
MONTHLY = [
    ("NETFLIX.COM 866-579-7172", 16.49, 3),
    ("SPOTIFY P2F41A9C", 11.99, 5),
    ("ROGERS ******4471", 65.00, 12),
    ("APPLE.COM/BILL 866-712-7753", 3.99, 18),
    ("GOODLIFE FITNESS CLUBS", 44.07, 1),
    ("CLAUDE.AI SUBSCRIPTION", 28.25, 9),
    ("RENT - 1155 RICHMOND ST PROPERTY MGMT", 950.00, 1),
]
ONE_OFF = [
    (2026, 8, 22, "CINEPLEX ENTERTAINMENT LONDON ON", -31.50),
    (2026, 9, 3, "BEST BUY #941", -129.99),
    (2026, 9, 14, "SHOPPERS DRUG MART #1294", -23.47),
    (2026, 9, 27, "BOUNCE ESCAPE ROOMS INC", -60.00),
    (2026, 10, 9, "LEVITT DENTAL GROUP", -85.00),
    (2026, 10, 18, "E-TRANSFER SENT Arjun Mehta", -40.00),
    (2026, 10, 25, "SQ *MAPLE LEAF BARBERSHOP", -28.00),
    (2026, 8, 30, "MONTHLY FEE", -4.95),
    (2026, 9, 30, "MONTHLY FEE", -4.95),
    (2026, 10, 30, "MONTHLY FEE", -4.95),
    (2026, 9, 5, "OSAP DISBURSEMENT", 3200.00),
]


def month_days(year, month):
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return [start + timedelta(days=i) for i in range((end - start).days)]


rows = []
for year, month in MONTHS:
    days = month_days(year, month)
    for desc, low, high, times in VARIABLE:
        for _ in range(times):
            rows.append((random.choice(days), desc, -round(random.uniform(low, high), 2)))
    for desc, amount, day in MONTHLY:
        rows.append((date(year, month, day), desc, -amount))
    # Part-time job, paid twice a month.
    for day in (15, 28):
        rows.append((date(year, month, day), "PAYROLL DEPOSIT HOMESENSE", round(random.uniform(380, 460), 2)))
    rows.append((date(year, month, 20), "E-TRANSFER RECEIVED Priya Sharma", 25.00))
for year, month, day, desc, amount in ONE_OFF:
    rows.append((date(year, month, day), desc, amount))

rows.sort()
OUT.parent.mkdir(exist_ok=True)
with OUT.open("w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["Date", "Description", "Amount"])
    for d, desc, amount in rows:
        writer.writerow([d.isoformat(), desc, f"{amount:.2f}"])
print(f"Wrote {len(rows)} transactions to {OUT}")
