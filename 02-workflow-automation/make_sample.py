"""Generate a deterministic messy CSV for the pipeline demo.

    python make_sample.py

Writes data/orders_messy.csv. Fixed seed and hand-written bad rows, so the
report is reproducible and every rejection reason is exercised on purpose
rather than left to chance.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

HEADER = ["order_id", "email", "order_date", "amount", "country", "note"]

# (row, why it is here)
GOOD = [
    ["AB-1001", "alice@example.com", "2026-01-15", "$1,234.50", "US", "first order"],
    ["AB-1002", "bob@example.com", "2026/02/03", "89.99", "GB", ""],
    ["AB-1003", "carol@example.com", "2026-03-07", "$0.99", "DE", "gift"],
]

# Every one of these should be rejected, with the reason noted.
BAD = [
    ["AB-1004", "", "2026-04-01", "10.00", "US", "email_missing"],
    ["AB-1005", "not-an-email", "2026-04-02", "10.00", "US", "email_invalid"],
    ["AB-1006", "dan@example.com", "05/03/2026", "10.00", "US", "date_ambiguous"],
    ["AB-1007", "eve@example.com", "not a date", "10.00", "US", "date_unparsable"],
    ["AB-1008", "frank@example.com", "2026-04-05", "-5.00", "US", "amount_negative"],
    ["AB-1009", "gina@example.com", "2026-04-06", "N/A", "US", "amount_missing"],
    ["BADID", "hugo@example.com", "2026-04-07", "10.00", "US", "order_id_bad_format"],
    ["AB-1010", "ivy@example.com", "2026-04-08", "10.00", "Wakanda", "country_unrecognised"],
    ["AB-1011", "jack@example.com", "2026-04-09", "1.234,50", "US", "amount_ambiguous"],
]

# Normalisation cases: messy but cleanable, must NOT be rejected.
NORMALISE = [
    [" ab-1012 ", "  MESSY@Example.COM  ", "2026-04-10", " $ 42.00 ", "uk", "  extra   spaces  "],
    ["AB-1013", "kim@example.com", "20260411", "0", "中国", "zero amount is valid"],
    ["AB-1014", "leo@example.com", "2026-04-12", "$1,000.00", "Germany", ""],
]

# Duplicate: same order_id as AB-1001.
DUPES = [
    ["AB-1001", "alice@example.com", "2026-01-15", "$1,234.50", "US", "duplicate submit"],
]


def main() -> None:
    rng = random.Random(20260913)  # fixed seed -> reproducible output

    rows = list(GOOD)
    # Filler good rows so the accept rate is realistic rather than 1-in-3.
    for i in range(200, 240):
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        rows.append(
            [
                f"CD-{i:04d}",
                f"user{i}@example.com",
                f"2026-{month:02d}-{day:02d}",
                f"{rng.uniform(5, 900):.2f}",
                rng.choice(["US", "GB", "DE", "CN", "FR"]),
                "",
            ]
        )

    rows += NORMALISE + BAD + DUPES
    rng.shuffle(rows)

    out = Path(__file__).parent / "data" / "orders_messy.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        writer.writerows(rows)

    print(f"wrote {out}  ({len(rows)} data rows)")
    print(f"  expected rejects: {len(BAD) + len(DUPES)}")
    print(f"  expected normalisations: {len(NORMALISE)}")


if __name__ == "__main__":
    main()
