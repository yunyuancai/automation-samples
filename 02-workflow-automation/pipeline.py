"""
A CSV cleaning and validation pipeline that refuses to guess.

The design rule behind every decision here:

    Silently "fixing" data is worse than rejecting it.
    A row that cannot be cleaned confidently is quarantined with a reason,
    never coerced into something plausible.

Why that matters: a pipeline that silently drops 3% of rows, or turns
"15/01/2026" into the wrong month, produces a report that looks clean and is
wrong. The failure surfaces weeks later in someone's accounting. This pipeline
exits non-zero on a fatal problem and writes an auditable report either way.

Run it:
    python make_sample.py                       # generate messy sample data
    python run.py --input data/orders_messy.csv --output out
    python test_pipeline.py                     # 39 tests, no pytest needed

Standard library only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class FatalError(Exception):
    """A problem that makes the whole run meaningless (bad schema, unreadable file)."""


# ---------------------------------------------------------------------------
# field cleaners
#
# Each returns (ok, cleaned, error). `error` is a short machine-ish tag that
# ends up verbatim in the rejection report, so it must be actionable.
# ---------------------------------------------------------------------------

_AMOUNT_JUNK = {"", "-", "n/a", "na", "null", "none", "nan", "tbd"}
_CURRENCY = re.compile(r"[^\d.\-]")


def clean_amount(raw: str) -> tuple[bool, str, str]:
    """Parse a currency-ish string into a plain decimal string.

    Accepts "$1,234.50", "1234.5", " 1 234,50 " is NOT accepted (ambiguous
    decimal separator) — that gets rejected rather than guessed.
    """
    s = (raw or "").strip()
    if s.lower() in _AMOUNT_JUNK:
        return False, "", "amount_missing"

    # Reject values with both separators in an ambiguous position.
    if "," in s and "." in s:
        # "1,234.50" is unambiguous (comma = thousands). "1.234,50" is EU style.
        if s.rindex(",") > s.rindex("."):
            return False, "", "amount_ambiguous_decimal_separator"
        s = s.replace(",", "")
    elif s.count(",") == 1 and len(s.split(",")[-1]) == 2:
        # "1234,50" almost certainly means 1234.50 in EU notation.
        return False, "", "amount_ambiguous_decimal_separator"
    else:
        s = s.replace(",", "")

    s = _CURRENCY.sub("", s)
    try:
        value = float(s)
    except ValueError:
        return False, "", f"amount_unparsable:{raw.strip()[:20]}"

    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return False, "", "amount_not_finite"
    if value < 0:
        return False, "", "amount_negative"

    return True, f"{value:.2f}", ""


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def clean_email(raw: str) -> tuple[bool, str, str]:
    s = (raw or "").strip().lower()
    if not s:
        return False, "", "email_missing"
    if not _EMAIL.match(s):
        return False, "", "email_invalid"
    return True, s, ""


_ORDER_ID = re.compile(r"^[A-Z]{2}-\d{4,6}$")


def clean_order_id(raw: str) -> tuple[bool, str, str]:
    s = (raw or "").strip().upper()
    if not s:
        return False, "", "order_id_missing"
    if not _ORDER_ID.match(s):
        return False, "", "order_id_bad_format"
    return True, s, ""


_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y",
                 "%m/%d/%Y", "%d.%m.%Y", "%Y%m%d")


def clean_date(raw: str, day_first: bool = True) -> tuple[bool, str, str]:
    """Parse a date to ISO 8601.

    Ambiguous dates (both components <= 12) are REJECTED, not guessed. This is
    the single most valuable thing this cleaner does — "05/03/2026" is five
    different days depending on who wrote it, and a pipeline that picks one
    silently is a liability.
    """
    s = (raw or "").strip()
    if not s:
        return False, "", "date_missing"

    # Explicit ISO is never ambiguous.
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if iso:
        try:
            return True, date(*map(int, iso.groups())).isoformat(), ""
        except ValueError as exc:
            return False, "", f"date_invalid:{exc}"

    # Detect slash/dot ambiguity before parsing.
    m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$", s)
    if m:
        a, b, _ = (int(g) for g in m.groups())
        if a <= 12 and b <= 12 and a != b:
            # Genuinely ambiguous -> refuse.
            return False, "", "date_ambiguous_day_month_order"

    for fmt in _DATE_FORMATS:
        try:
            return True, datetime.strptime(s, fmt).date().isoformat(), ""
        except ValueError:
            continue
    return False, "", f"date_unparsable:{s[:20]}"


_COUNTRY = re.compile(r"^[A-Z]{2}$")
_COUNTRY_ALIASES = {
    "UK": "GB",            # ISO 3166 is GB, not UK — normalise, don't reject
    "USA": "US",
    "GERMANY": "DE",
    "DEUTSCHLAND": "DE",
    "中国": "CN",
    "CHINA": "CN",
}


def clean_country(raw: str) -> tuple[bool, str, str]:
    s = (raw or "").strip().upper()
    if not s:
        return False, "", "country_missing"
    mapped = _COUNTRY_ALIASES.get(s, s)
    if not _COUNTRY.match(mapped):
        return False, "", f"country_unrecognised:{raw.strip()[:16]}"
    return True, mapped, ""


def clean_text(raw: str) -> tuple[bool, str, str]:
    return True, " ".join((raw or "").split()), ""


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


@dataclass
class Field:
    name: str
    cleaner: Callable[[str], tuple[bool, str, str]]
    required: bool = True


@dataclass
class Schema:
    fields: list[Field]

    @property
    def required_names(self) -> list[str]:
        return [f.name for f in self.fields if f.required]

    @property
    def all_names(self) -> list[str]:
        return [f.name for f in self.fields]


DEFAULT_SCHEMA = Schema(
    fields=[
        Field("order_id", clean_order_id),
        Field("email", clean_email),
        Field("order_date", clean_date),
        Field("amount", clean_amount),
        Field("country", clean_country),
        Field("note", clean_text, required=False),
    ]
)


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass
class Rejected:
    row_number: int
    reasons: list[str]
    raw: dict

    def to_row(self) -> dict:
        return {"_row": self.row_number, "_reasons": "; ".join(self.reasons), **self.raw}


@dataclass
class Report:
    total: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    reason_counts: dict[str, int] = field(default_factory=dict)
    fatal: str | None = None
    input_hash: str = ""

    @property
    def accept_rate(self) -> float:
        return self.accepted / self.total if self.total else 0.0

    def to_dict(self) -> dict:
        return {
            "input_sha256": self.input_hash,
            "rows_total": self.total,
            "rows_accepted": self.accepted,
            "rows_rejected": self.rejected,
            "rows_duplicate_dropped": self.duplicates,
            "accept_rate": round(self.accept_rate, 4),
            "fatal": self.fatal,
            "rejection_reasons": dict(
                sorted(self.reason_counts.items(), key=lambda kv: -kv[1])
            ),
        }

    def render(self) -> str:
        w = 68
        out = ["=" * w, "VALIDATION REPORT", "=" * w]
        if self.input_hash:
            out.append(f"input sha256 : {self.input_hash[:16]}...")
        if self.fatal:
            out += ["", f"** FATAL: {self.fatal}", "", "No output written.", "=" * w]
            return "\n".join(out)

        out += [
            f"rows read    : {self.total}",
            f"accepted     : {self.accepted}  ({self.accept_rate:.1%})",
            f"rejected     : {self.rejected}",
            f"dup dropped  : {self.duplicates}",
        ]
        if self.reason_counts:
            out += ["", "rejection reasons (most common first):"]
            for reason, n in sorted(self.reason_counts.items(), key=lambda kv: -kv[1]):
                out.append(f"  {n:>5}  {reason}")
        if self.rejected:
            out += [
                "",
                "Rejected rows are in the *_rejected.csv output with a _reasons",
                "column. They were NOT silently repaired.",
            ]
        out.append("=" * w)
        return "\n".join(out)


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    accepted: list[dict]
    rejected: list[Rejected]
    report: Report
    fatal: str | None = None


class Pipeline:
    def __init__(self, schema: Schema | None = None, dedupe_on: str = "order_id") -> None:
        self.schema = schema or DEFAULT_SCHEMA
        self.dedupe_on = dedupe_on

    # -- input ----------------------------------------------------------

    @staticmethod
    def read_csv(path: str | Path) -> tuple[list[dict], str]:
        p = Path(path)
        if not p.exists():
            raise FatalError(f"input file not found: {p}")
        raw_bytes = p.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()

        text = raw_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise FatalError("input has no header row")
        rows = [dict(r) for r in reader]
        return rows, digest

    def validate_header(self, fieldnames: Sequence[str]) -> None:
        """Fail loudly on a schema mismatch instead of producing empty output."""
        missing = [n for n in self.schema.required_names if n not in fieldnames]
        if missing:
            raise FatalError(
                f"missing required column(s): {', '.join(missing)} "
                f"(found: {', '.join(fieldnames)})"
            )

    # -- core -----------------------------------------------------------

    def process_row(self, row: dict) -> tuple[bool, dict, list[str]]:
        cleaned: dict = {}
        reasons: list[str] = []
        for f in self.schema.fields:
            raw = row.get(f.name, "")
            ok, value, err = f.cleaner(raw if raw is not None else "")
            if ok:
                cleaned[f.name] = value
            elif f.required:
                reasons.append(err)
            else:
                cleaned[f.name] = ""
                if err:
                    reasons.append(f"{f.name}:{err}")
        return (not reasons), cleaned, reasons

    def run(self, rows: Iterable[dict]) -> PipelineResult:
        accepted: list[dict] = []
        rejected: list[Rejected] = []
        report = Report()
        seen: dict[str, int] = {}

        for i, row in enumerate(rows, start=2):   # row 1 is the header
            report.total += 1
            ok, cleaned, reasons = self.process_row(row)

            if not ok:
                rejected.append(Rejected(row_number=i, reasons=reasons, raw=row))
                report.rejected += 1
                for r in reasons:
                    key = r.split(":")[0]
                    report.reason_counts[key] = report.reason_counts.get(key, 0) + 1
                continue

            key = cleaned.get(self.dedupe_on, "")
            if key and key in seen:
                reasons = [f"duplicate_{self.dedupe_on}:first_seen_row_{seen[key]}"]
                rejected.append(Rejected(row_number=i, reasons=reasons, raw=row))
                report.rejected += 1
                report.duplicates += 1
                report.reason_counts[reasons[0].split(":")[0]] = (
                    report.reason_counts.get(reasons[0].split(":")[0], 0) + 1
                )
                continue

            if key:
                seen[key] = i
            accepted.append(cleaned)
            report.accepted += 1

        return PipelineResult(accepted=accepted, rejected=rejected, report=report)

    # -- convenience ----------------------------------------------------

    def run_file(self, path: str | Path, outdir: str | Path) -> PipelineResult:
        rows, digest = self.read_csv(path)
        if rows:
            self.validate_header(list(rows[0].keys()))

        result = self.run(rows)
        result.report.input_hash = digest

        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        self.write_outputs(result, out)
        return result

    def write_outputs(self, result: PipelineResult, outdir: Path) -> None:
        clean_path = outdir / "orders_clean.csv"
        with clean_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.schema.all_names)
            writer.writeheader()
            writer.writerows(result.accepted)

        rejected_path = outdir / "orders_rejected.csv"
        raw_columns = list(result.rejected[0].raw.keys()) if result.rejected else []
        with rejected_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["_row", "_reasons", *raw_columns], extrasaction="ignore"
            )
            writer.writeheader()
            for r in result.rejected:
                writer.writerow(r.to_row())

        (outdir / "report.json").write_text(
            json.dumps(result.report.to_dict(), indent=2), encoding="utf-8"
        )
        (outdir / "report.txt").write_text(result.report.render(), encoding="utf-8")
