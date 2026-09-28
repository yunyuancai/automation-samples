# Engineering Parameter Table Extractor

Turns vendor-style parameter dumps — the kind shipped as fixed-width text,
Excel exports or tool-internal tables — into structured JSON/CSV, with
**radix correctness, cross-source collision detection, and a full report of
what could not be parsed.**

**Standard library only.** Python 3.10+.

```bash
python run.py --input samples/mr_j4a.txt --output out
python run.py --input samples/mr_j4a.txt --input samples/mr_j4a_rev2.txt \
              --output out --strict --fail-on-collision
python test_extractor.py            # 39 tests, no pytest needed
```

---

## The three problems that actually matter

Parsing the text is the easy part. These are the parts that cause real damage:

### 1. Radix

`1000-1265h` is a hex range. `0-100` is decimal. Get it wrong and you are off by
a factor of 4096 — on a parameter that gets written to a servo drive.

The rule here: **the `h` suffix is the only signal.** A bare number is decimal,
never guessed as hex from its digits. `10` is ten, not sixteen.

And a range whose bounds disagree about radix is rejected outright:

```
0000-FFFF      ->  rejected: range_mixed_radix
0000-FFFFh     ->  accepted: 0 .. 65535, hex
```

`0` is the same number either way, so the value is not ambiguous — but the
*intent* is, and a mixed-radix range usually means the source table is corrupt.
Refusing is cheaper than debugging it later.

### 2. Collisions

Two vendor files can define the same parameter id differently. Whichever loads
last wins, silently, and the configurator writes the wrong value to hardware.

```
COLLISIONS (same id, different definition):
  PA02  differs in: maximum
      mr_j4a.txt:5        unit=-     range=0000-0003h  default=0000h
      mr_j4a_rev2.txt:4   unit=-     range=0000-0007h  default=0000h
  PS01  differs in: unit, maximum, default
      mr_j4a.txt:27       unit=r/min range=0-100       default=50
      mr_j4a_rev2.txt:7   unit=rpm   range=0-120       default=60

Pick a canonical source deliberately. Do not let load order decide.
```

The `PS01` case is the one that bites: `r/min` vs `rpm` is the same physical
unit written two ways, and the limits differ by 20%. Nothing in either file
looks wrong on its own.

### 3. Coverage

Lines that fail to parse are the interesting ones. Every record carries its
**source file and line number**, and every failure is written to `unparsed.txt`
with a machine-readable reason:

```
     1  wrong_field_count
     1  range_mixed_radix
     1  bad_parameter_id
     1  default_out_of_range
     1  default_radix_mismatch
     1  range_inverted

Full detail in unparsed.txt. These were NOT silently dropped.
```

A parser that skips 8% of rows and reports success is worse than one that
crashes, because you ship the result.

## Validated invariants

Beyond parsing, each record is checked for internal consistency. These are all
rejected with a specific reason:

| Condition | Example | Reason |
|---|---|---|
| Default outside declared range | `0000-0005h` / default `0009h` | `default_out_of_range` |
| Default radix ≠ range radix | `0000-0005h` / default `3` | `default_radix_mismatch` |
| Inverted range | `0100-0050h` | `range_inverted` |
| Mixed-radix range | `0000-FFFF` | `range_mixed_radix` |
| Malformed id | `XX01` | `bad_parameter_id` |

## Outputs

| File | Contents |
|---|---|
| `params.json` | full records + per-source counts + collisions, with a `schema` version |
| `params.csv` | flat table, `minimum`/`maximum`/`default` as integers, plus `source` and `line` |
| `unparsed.txt` | `source:line  reason  raw text` for every line that failed |
| `report.txt` | the human-readable report shown above |

Every record carries provenance:

```json
{
  "id": "PA01", "group": "PA", "symbol": "*STY", "name": "Operation mode",
  "unit": "", "range_raw": "1000-1265h",
  "minimum": 4096, "maximum": 4709, "radix": "hex",
  "default_raw": "1000h", "default": 4096,
  "source": "mr_j4a.txt", "line": 4
}
```

## Exit codes

Built to gate a build or release pipeline:

| Code | Meaning |
|---|---|
| 0 | ran, output written |
| 2 | **fatal** — nothing parsed, file missing, or `--strict` with unparsed lines |
| 4 | collisions found and `--fail-on-collision` was set |

Wire `--strict --fail-on-collision` into CI and a vendor table that changed
shape, or two sources that drifted apart, fails the build instead of producing a
plausible-looking config.

## Verification

```
$ python test_extractor.py
39 passed, 0 failed, 39 total
```

Includes the radix rules in both directions (bare-number-is-decimal, `h`-suffix-
is-hex, mixed-radix rejection), all six rejection reasons on the sample data,
collision detection for single-field and multi-field divergence, provenance
(source + line) being preserved through extraction, `FatalError` on missing
files and all-unparsable input, all four output files, and all four CLI exit
codes.

## Known limitations

- **The format is pipe-delimited, not auto-detected.** Real vendor dumps also
  come as fixed-width and as Excel. Adding a format sniffer means writing
  `detect_format()` and a second `parse_line`; the record, collision and report
  layers are format-agnostic and would not change.
- **No `.xlsx` ingestion.** Converting via CSV is assumed. Reading `.xlsx`
  without a third-party library is possible (it is a zip of XML) but was out of
  scope here.
- **Radix inference needs the suffix.** A hex range written without `h` and
  without any a–f digit is indistinguishable from decimal and will be read as
  decimal. The mixed-radix check catches the case where a–f digits are present;
  it cannot catch `0000-0100` meaning hex.
- **Collision fields are compared, not semantically matched.** `r/min` vs `rpm`
  is reported as a unit difference, not recognised as the same unit. Normalising
  units needs a unit table and is deliberately not guessed at here.
- **No schema validation against a known-good vendor baseline.** A golden-file
  comparison would catch additions and removals, not just disagreements.

## Files

| File | Lines | Contents |
|---|---|---|
| `extractor.py` | 349 | parsers, collision detection, extraction, report, writers |
| `test_extractor.py` | 251 | 39 tests, plain-assert, runs without pytest |
| `run.py` | 57 | CLI with the three exit codes |
| `samples/mr_j4a.txt` | 28 | 25 data lines, 6 deliberately malformed |
| `samples/mr_j4a_rev2.txt` | 8 | second source, 2 deliberate collisions with the first |

---

*Built as a portfolio sample. The approach mirrors production work extracting
large parameter tables from vendor tooling into structured form; this is a
self-contained version with synthetic data and no external dependencies.*
