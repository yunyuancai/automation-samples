"""Run every portfolio sample's test suite and summarise.

    python run_all_tests.py
    py     run_all_tests.py     # Windows, if `python` is the Store stub

Exits 0 only if all suites pass — so this can be the thing you show on a call:
one command, one green summary.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

SUITES = [
    ("01-ai-agent-memory", "test_memory_agent.py"),
    ("02-workflow-automation", "test_pipeline.py"),
    ("03-doc-extraction", "test_extractor.py"),
]


def main() -> int:
    total_pass = total_fail = 0
    failures: list[str] = []

    for folder, test_file in SUITES:
        path = ROOT / folder / test_file
        print(f"{'=' * 66}\n{folder}  ->  {test_file}\n{'=' * 66}")

        proc = subprocess.run(
            [sys.executable, test_file],
            cwd=path.parent,
            capture_output=True,
            text=True,
        )
        tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        summary = tail[-1] if tail else "(no output)"

        # Parse "N passed, M failed, T total".
        passed = failed = 0
        for part in summary.split(","):
            part = part.strip()
            if part.endswith("passed"):
                passed = int(part.split()[0])
            elif part.endswith("failed"):
                failed = int(part.split()[0])
        total_pass += passed
        total_fail += failed

        print(summary)
        if proc.returncode != 0:
            failures.append(f"{folder}/{test_file}")
            for ln in tail:
                if "FAIL" in ln:
                    print(f"  {ln}")

    print(f"\n{'=' * 66}")
    print(f"TOTAL: {total_pass} passed, {total_fail} failed "
          f"across {len(SUITES)} suites")
    if failures:
        print("FAILING SUITES: " + ", ".join(failures))
    print("=" * 66)
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
