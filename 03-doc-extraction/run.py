"""CLI for the parameter-table extractor.

    python run.py --input samples/mr_j4a.txt --output out
    python run.py --input samples/mr_j4a.txt --input samples/mr_j4a_rev2.txt \
                  --output out --strict --fail-on-collision

Exit codes:

    0   ran, output written
    2   fatal: nothing parsed, or --strict and unparsed lines remain
    4   collisions found and --fail-on-collision was set

--strict and --fail-on-collision exist so this can gate a build or a release
pipeline. An extract that silently loses 8% of rows, or that lets two vendor
files disagree about a parameter's valid range, should fail loudly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from extractor import FatalError, extract, render_report, write_outputs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Extract structured parameters from vendor-style tables."
    )
    ap.add_argument("--input", action="append", required=True,
                    help="input file (repeatable)")
    ap.add_argument("--output", default="out", help="output directory (default: out)")
    ap.add_argument("--strict", action="store_true",
                    help="treat any unparsed line as fatal (exit 2)")
    ap.add_argument("--fail-on-collision", action="store_true",
                    help="exit 4 if any parameter id is defined inconsistently")
    args = ap.parse_args(argv)

    try:
        result = extract(args.input)
    except FatalError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2

    write_outputs(result, args.output)
    print(render_report(result))

    out = Path(args.output)
    print(f"\nwritten to {out}/")
    for name in ("params.json", "params.csv", "unparsed.txt", "report.txt"):
        p = out / name
        if p.exists():
            print(f"  {name:<16} {p.stat().st_size:>8} bytes")

    if args.strict and result.unparsed:
        print(
            f"\nFAIL: --strict set and {len(result.unparsed)} line(s) did not parse.",
            file=sys.stderr,
        )
        return 2

    if args.fail_on_collision and result.collisions:
        print(
            f"\nFAIL: {len(result.collisions)} collision(s) across sources.",
            file=sys.stderr,
        )
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
