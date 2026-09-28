"""CLI for the cleaning pipeline.

    python run.py --input data/orders_messy.csv --output out

Exit codes (these matter — a scheduler needs to know the difference):

    0   ran, output written
    2   fatal: the run produced nothing meaningful (bad file / missing columns)
    3   ran, but the rejection rate exceeded --max-reject-rate

Exit code 3 exists so a cron job or n8n workflow can alert on "the input
format changed" instead of quietly ingesting a 60%-rejected dataset.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline import FatalError, Pipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Clean and validate a CSV export.")
    ap.add_argument("--input", required=True, help="input CSV path")
    ap.add_argument("--output", default="out", help="output directory (default: out)")
    ap.add_argument(
        "--max-reject-rate",
        type=float,
        default=0.25,
        help="exit 3 if the rejection rate exceeds this (default: 0.25)",
    )
    args = ap.parse_args(argv)

    pipe = Pipeline()
    try:
        result = pipe.run_file(args.input, args.output)
    except FatalError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2

    print(result.report.render())

    out = Path(args.output)
    print(f"\nwritten to {out}/")
    for name in ("orders_clean.csv", "orders_rejected.csv", "report.json", "report.txt"):
        p = out / name
        if p.exists():
            print(f"  {name:<24} {p.stat().st_size:>8} bytes")

    if result.report.total == 0:
        print("\nWARNING: input had a header but no data rows.", file=sys.stderr)
        return 3

    rate = result.report.rejected / result.report.total
    if rate > args.max_reject_rate:
        print(
            f"\nWARNING: rejection rate {rate:.1%} exceeds "
            f"--max-reject-rate {args.max_reject_rate:.1%}.",
            file=sys.stderr,
        )
        print(
            "This usually means the upstream export format changed. "
            "Check the rejection reasons above before trusting the clean file.",
            file=sys.stderr,
        )
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
