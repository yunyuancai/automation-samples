"""
Extract structured parameter tables from vendor-style engineering dumps.

This is the "get data out of documents designed to be read by humans" problem.
The hard parts are not the parsing — they are:

  1. Radix.       "1000-1265h" is a hex range. "0-100" is decimal. Mixing them
                  up is a 4096x error, and it is silent.
  2. Collisions.  Two vendor files can define the same parameter id with
                  different ranges, units or defaults. Whichever is loaded last
                  wins, quietly, and the tool writes the wrong value to hardware.
  3. Coverage.    Lines that fail to parse are the interesting ones. A parser
                  that skips them reports success on an incomplete extract.

So: every record carries its source and line number, collisions are detected
across all sources and reported field-by-field, and unparsed lines are written
to their own file rather than dropped.

Standard library only.

    python run.py --input samples/mr_j4a.txt --output out
    python run.py --input samples/mr_j4a.txt --input samples/mr_j4a_rev2.txt \
                  --output out --strict --fail-on-collision
    python test_extractor.py            # 39 tests, no pytest needed
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class FatalError(Exception):
    """The run cannot produce a trustworthy result."""


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


@dataclass
class Param:
    id: str
    group: str
    symbol: str
    name: str
    unit: str
    range_raw: str
    minimum: int | None
    maximum: int | None
    radix: str                       # "hex" | "dec" | ""
    default_raw: str
    default: int | None
    source: str
    line: int

    def to_dict(self) -> dict:
        return asdict(self)

    def definition(self) -> tuple:
        """The fields that must agree across sources for a record to be the same."""
        return (
            self.symbol, self.name, self.unit,
            self.minimum, self.maximum, self.radix, self.default,
        )


@dataclass
class Unparsed:
    source: str
    line: int
    text: str
    reason: str

    def to_row(self) -> dict:
        return {"source": self.source, "line": self.line,
                "reason": self.reason, "text": self.text}


@dataclass
class Collision:
    param_id: str
    variants: list[dict] = field(default_factory=list)
    differing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# field parsers
# ---------------------------------------------------------------------------

_ID = re.compile(r"^P[A-Z]\d{2}$")
_SYMBOL = re.compile(r"^[*A-Za-z0-9_]{1,8}$")
_RANGE = re.compile(r"^(?P<lo>[0-9A-Fa-f]{1,6})\s*-\s*(?P<hi>[0-9A-Fa-f]{1,6})(?P<suffix>[hH])?$")
_VALUE = re.compile(r"^(?P<num>[0-9A-Fa-f]{1,6})(?P<suffix>[hH])?$")


def parse_number(raw: str) -> tuple[bool, int | None, str, str]:
    """Parse a value that may carry an 'h' hex suffix.

    Returns (ok, value, radix, error). A bare number is decimal — the suffix is
    the only signal, and guessing radix from the digit set would turn "10" into
    16 for no reason.
    """
    s = (raw or "").strip()
    if not s:
        return False, None, "", "value_empty"
    m = _VALUE.match(s)
    if not m:
        return False, None, "", f"value_unparsable:{s[:16]}"
    is_hex = bool(m.group("suffix"))
    try:
        return True, int(m.group("num"), 16 if is_hex else 10), "hex" if is_hex else "dec", ""
    except ValueError as exc:
        return False, None, "", f"value_invalid:{exc}"


def parse_range(raw: str) -> tuple[bool, int | None, int | None, str, str]:
    """Parse "1000-1265h", "0-100", or "-" (no range).

    Both bounds must share a radix. "0-FFFFh" is rejected because 0 is
    ambiguous — it is the same number either way, but the intent is not, and a
    mixed-radix range usually means the source data is corrupt.
    """
    s = (raw or "").strip()
    if s in ("", "-", "n/a", "N/A"):
        return True, None, None, "", ""

    m = _RANGE.match(s)
    if not m:
        return False, None, None, "", f"range_unparsable:{s[:20]}"

    hex_suffix = bool(m.group("suffix"))
    lo_s, hi_s = m.group("lo"), m.group("hi")

    # If one bound contains a-f it must be hex even without the suffix.
    looks_hex = any(c in "abcdefABCDEF" for c in lo_s + hi_s)
    if looks_hex and not hex_suffix:
        return False, None, None, "", f"range_mixed_radix:{s[:20]}"

    base = 16 if (hex_suffix or looks_hex) else 10
    try:
        lo, hi = int(lo_s, base), int(hi_s, base)
    except ValueError as exc:
        return False, None, None, "", f"range_invalid:{exc}"

    if lo > hi:
        return False, None, None, "", f"range_inverted:{lo}>{hi}"

    return True, lo, hi, "hex" if base == 16 else "dec", ""


# ---------------------------------------------------------------------------
# line parser
# ---------------------------------------------------------------------------

_FIELDS = ("id", "symbol", "name", "unit", "range", "default")


def parse_line(text: str, source: str = "", line: int = 0) -> tuple[bool, Param | None, str]:
    """Parse one pipe-delimited record.

    Expected:  ID | SYMBOL | NAME | UNIT | RANGE | DEFAULT
    """
    if not text.strip():
        return False, None, "blank"
    if text.lstrip().startswith("#"):
        return False, None, "comment"

    parts = [p.strip() for p in text.split("|")]
    if len(parts) != len(_FIELDS):
        return False, None, f"wrong_field_count:{len(parts)}!= {len(_FIELDS)}"

    rec = dict(zip(_FIELDS, parts))

    pid = rec["id"].upper()
    if not _ID.match(pid):
        return False, None, f"bad_parameter_id:{rec['id'][:12]}"

    if not _SYMBOL.match(rec["symbol"]):
        return False, None, f"bad_symbol:{rec['symbol'][:12]}"

    if not rec["name"]:
        return False, None, "name_empty"

    ok, lo, hi, radix, err = parse_range(rec["range"])
    if not ok:
        return False, None, err

    ok2, default, radix2, err2 = parse_number(rec["default"])
    if not ok2 and rec["default"] not in ("", "-"):
        return False, None, err2

    # If the range is hex, the default must be too (and vice versa).
    if radix and radix2 and radix != radix2:
        return False, None, f"default_radix_mismatch:{radix2}!={radix}"

    final_radix = radix or radix2 or ""

    if default is not None and lo is not None and not (lo <= default <= hi):
        return False, None, f"default_out_of_range:{default}"

    return (
        True,
        Param(
            id=pid,
            group=pid[:2],
            symbol=rec["symbol"],
            name=rec["name"],
            unit="" if rec["unit"] in ("-", "") else rec["unit"],
            range_raw=rec["range"],
            minimum=lo,
            maximum=hi,
            radix=final_radix,
            default_raw=rec["default"],
            default=default,
            source=source,
            line=line,
        ),
        "",
    )


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


@dataclass
class Extraction:
    params: list[Param]
    unparsed: list[Unparsed]
    collisions: list[Collision]
    sources: list[dict]

    def to_dict(self) -> dict:
        return {
            "schema": 1,
            "sources": self.sources,
            "counts": {
                "parsed": len(self.params),
                "unparsed": len(self.unparsed),
                "collisions": len(self.collisions),
            },
            "collisions": [c.to_dict() for c in self.collisions],
            "params": [p.to_dict() for p in self.params],
        }


def extract_file(path: str | Path) -> tuple[list[Param], list[Unparsed]]:
    p = Path(path)
    if not p.exists():
        raise FatalError(f"input file not found: {p}")
    text = p.read_text(encoding="utf-8", errors="replace")

    params: list[Param] = []
    unparsed: list[Unparsed] = []
    for i, line in enumerate(text.splitlines(), start=1):
        ok, param, reason = parse_line(line, source=p.name, line=i)
        if ok and param is not None:
            params.append(param)
        elif reason not in ("blank", "comment"):
            # Blank lines and comments are not failures; anything else is.
            unparsed.append(Unparsed(p.name, i, line.strip()[:120], reason))
    return params, unparsed


def detect_collisions(params: Iterable[Param]) -> list[Collision]:
    """Find parameter ids defined differently by different sources."""
    by_id: dict[str, list[Param]] = {}
    for p in params:
        by_id.setdefault(p.id, []).append(p)

    collisions: list[Collision] = []
    for pid, group in sorted(by_id.items()):
        if len(group) < 2:
            continue
        base = group[0]
        differing: list[str] = []
        for other in group[1:]:
            for name, a, b in zip(
                ("symbol", "name", "unit", "minimum", "maximum", "radix", "default"),
                base.definition(),
                other.definition(),
            ):
                if a != b and name not in differing:
                    differing.append(name)
        if differing:
            collisions.append(
                Collision(
                    param_id=pid,
                    variants=[
                        {"source": v.source, "line": v.line, "symbol": v.symbol,
                         "unit": v.unit, "range": v.range_raw, "default": v.default_raw}
                        for v in group
                    ],
                    differing_fields=differing,
                )
            )
    return collisions


def dedupe_keep_first(params: Iterable[Param]) -> list[Param]:
    """One record per id; later duplicates are dropped (collisions are reported
    separately so this is a display decision, not a silent one)."""
    seen: set[str] = set()
    out: list[Param] = []
    for p in params:
        if p.id in seen:
            continue
        seen.add(p.id)
        out.append(p)
    return out


def extract(paths: list[str | Path]) -> Extraction:
    if not paths:
        raise FatalError("no input files given")

    all_params: list[Param] = []
    all_unparsed: list[Unparsed] = []
    sources: list[dict] = []

    for path in paths:
        params, unparsed = extract_file(path)
        all_params += params
        all_unparsed += unparsed
        sources.append(
            {
                "file": Path(path).name,
                "parsed": len(params),
                "unparsed": len(unparsed),
                "groups": sorted({p.group for p in params}),
            }
        )

    if not all_params:
        raise FatalError(
            "no parameters parsed from any source — check the format "
            "(expected: ID | SYMBOL | NAME | UNIT | RANGE | DEFAULT)"
        )

    return Extraction(
        params=dedupe_keep_first(all_params),
        unparsed=all_unparsed,
        collisions=detect_collisions(all_params),
        sources=sources,
    )


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

_CSV_COLUMNS = ["id", "group", "symbol", "name", "unit", "range_raw",
                "minimum", "maximum", "radix", "default_raw", "default",
                "source", "line"]


def write_outputs(result: Extraction, outdir: str | Path) -> None:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    (out / "params.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    with (out / "params.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for p in result.params:
            writer.writerow(p.to_dict())

    with (out / "unparsed.txt").open("w", encoding="utf-8") as fh:
        if not result.unparsed:
            fh.write("(none)\n")
        for u in result.unparsed:
            fh.write(f"{u.source}:{u.line}\t{u.reason}\t{u.text}\n")

    (out / "report.txt").write_text(render_report(result), encoding="utf-8")


def render_report(result: Extraction) -> str:
    w = 70
    lines = ["=" * w, "EXTRACTION REPORT", "=" * w, "", "sources:"]
    for s in result.sources:
        flag = "  <-- unparsed lines" if s["unparsed"] else ""
        lines.append(
            f"  {s['file']:<24} parsed={s['parsed']:<5} "
            f"unparsed={s['unparsed']:<4}{flag}"
        )

    lines += [
        "",
        f"parameters   : {len(result.params)} unique",
        f"unparsed     : {len(result.unparsed)}",
        f"collisions   : {len(result.collisions)}",
    ]

    if result.unparsed:
        lines += ["", "unparsed lines by reason:"]
        counts: dict[str, int] = {}
        for u in result.unparsed:
            key = u.reason.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
        for reason, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {n:>4}  {reason}")
        lines.append("")
        lines.append("Full detail in unparsed.txt. These were NOT silently dropped.")

    if result.collisions:
        lines += ["", "COLLISIONS (same id, different definition):"]
        for c in result.collisions:
            lines.append(f"  {c.param_id}  differs in: {', '.join(c.differing_fields)}")
            for v in c.variants:
                lines.append(
                    f"      {v['source']}:{v['line']}  "
                    f"unit={v['unit'] or '-'} range={v['range']} default={v['default']}"
                )
        lines += [
            "",
            "Pick a canonical source deliberately. Do not let load order decide.",
        ]

    lines.append("=" * w)
    return "\n".join(lines)
