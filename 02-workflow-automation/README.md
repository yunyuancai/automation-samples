# CSV Cleaning & Validation Pipeline

A data pipeline built around one rule: **silently "fixing" data is worse than
rejecting it.**

Rows that cannot be cleaned confidently are quarantined with a machine-readable
reason. Ambiguous values are refused rather than guessed. Fatal problems exit
non-zero. Either way you get an auditable report.

**Standard library only.** Python 3.10+.

```bash
python make_sample.py                                   # generate messy sample data
python run.py --input data/orders_messy.csv --output out # run it
python test_pipeline.py                                  # 39 tests, no pytest needed
```

---

## The problem with most cleaning scripts

They look like this:

```python
df["date"] = pd.to_datetime(df["date"], errors="coerce")
df["amount"] = df["amount"].str.replace(r"[$,]", "", regex=True).astype(float)
df = df.dropna()
```

This runs. It also:

- Turns `05/03/2026` into a date, and **picks a month for you**. It is wrong for
  half the world and you will not find out for months.
- Drops every row that failed coercion — silently. A 3% silent loss looks like a
  clean dataset.
- Converts `1.234,50` to `1.23450` in some locales, i.e. off by 1000×.
- Produces no record of what was dropped or why.

The failure does not show up in the pipeline. It shows up in someone's
accounting, weeks later, with no way to trace it back.

## What this does instead

```
input.csv
    │
    ├─ header check ──────── missing required column ──▶ FATAL, exit 2, no output
    │
    └─ per row
         │
         ├─ clean confidently ──▶ orders_clean.csv
         │
         └─ cannot ────────────▶ orders_rejected.csv   (+ _reasons column)
                                            │
                                            └──▶ report.txt / report.json
```

### Shown output

```
====================================================================
VALIDATION REPORT
====================================================================
input sha256 : fe6fa16a6e388588...
rows read    : 56
accepted     : 46  (82.1%)
rejected     : 10
dup dropped  : 1

rejection reasons (most common first):
      1  order_id_bad_format
      1  duplicate_order_id
      1  date_ambiguous_day_month_order
      1  amount_negative
      1  email_invalid
      1  email_missing
      1  amount_ambiguous_decimal_separator
      1  date_unparsable
      1  amount_missing
      1  country_unrecognised

Rejected rows are in the *_rejected.csv output with a _reasons
column. They were NOT silently repaired.
====================================================================
```

And the rejected file keeps the original data plus the reason:

| _row | _reasons | order_id | order_date | amount |
|---|---|---|---|---|
| 7 | `order_id_bad_format` | BADID | 2026-04-07 | 10.00 |
| 8 | `duplicate_order_id:first_seen_row_6` | AB-1001 | 2026-01-15 | $1,234.50 |
| 11 | `date_ambiguous_day_month_order` | AB-1006 | 05/03/2026 | 10.00 |
| 15 | `amount_negative` | AB-1008 | 2026-04-05 | -5.00 |

Note row 8: the reason names **the row the original was on**. That is what makes
a rejected file actionable instead of just a pile of failures.

## The three decisions that matter

**1. Ambiguous dates are rejected, not guessed.**
`05/03/2026` is a valid date in both US and EU notation, and they are different
days. The pipeline refuses it. `15/01/2026` is unambiguous (15 is not a month),
so that one parses. `03/03/2026` is accepted because both readings agree.

**2. Ambiguous number formats are rejected.**
`$1,234.50` is unambiguously 1234.50. `1.234,50` is not — it is 1234.50 in EU
notation but 1.2345 if the dot is the decimal point. Off by 1000× is not a
rounding error, so the row is quarantined.

**3. Cleaning that *is* safe still happens.**
Rejecting everything is not the goal. These are normalised, not rejected:

| Input | Output |
|---|---|
| `"  MESSY@Example.COM  "` | `messy@example.com` |
| `"uk"` | `GB` — `UK` is not an ISO 3166 code, `GB` is |
| `"Germany"` | `DE` |
| `"中国"` | `CN` |
| `" $ 42.00 "` | `42.00` |
| `"20260411"` | `2026-04-11` |

The line between "normalise" and "reject" is whether the transformation is
**provably lossless**. Case-folding an email is. Choosing a month is not.

## Exit codes

A scheduler needs to distinguish "fine" from "the input format changed":

| Code | Meaning |
|---|---|
| 0 | ran, output written, rejection rate within tolerance |
| 2 | **fatal** — file missing, no header, required column absent, nothing written |
| 3 | ran, but rejection rate exceeded `--max-reject-rate` (default 25%) |

Exit 3 is the useful one. Wire it to an alert and you find out that a supplier
changed their export format on the day it happens, instead of noticing that
revenue numbers drifted a month later.

## Verification

```
$ python test_pipeline.py
39 passed, 0 failed, 39 total
```

Covers every cleaner across valid / junk / ambiguous / boundary inputs, the
ambiguity refusals in both the date and the number parser, country alias
normalisation including CJK, header validation raising `FatalError` with the
offending column named, duplicate detection preserving which row was first,
rejection reasons being recorded per-row and aggregated per-run, all four
outputs being written with a correct SHA-256 of the input, and all three CLI
exit codes.

## Known limitations

- **Whole file is loaded into memory.** Fine to ~100k rows; a real large
  deployment needs chunked reads. The `run()` method already takes an iterable,
  so streaming is a matter of changing the reader.
- **The schema is declared, not inferred.** This is deliberate — inferred
  schemas are exactly the silent-guessing failure mode this pipeline exists to
  avoid — but it does mean you edit `DEFAULT_SCHEMA` for a new dataset.
- **Date parsing is a fixed format list, no locale database.** Unknown formats
  are rejected, which is correct behaviour but will need extending for some
  locales.
- **No PII handling.** Emails pass through in the clear. Redaction/hashing would
  be a field cleaner, but it is not implemented.
- **Duplicate detection is exact-match on `order_id`.** `find_near_duplicate`-style
  fuzzy matching is not used here; near-duplicate orders will both be accepted.

## Files

| File | Lines | Contents |
|---|---|---|
| `pipeline.py` | 334 | cleaners, schema, report, pipeline, writers |
| `test_pipeline.py` | 239 | 39 tests, plain-assert, runs without pytest |
| `make_sample.py` | 69 | deterministic messy-sample generator |
| `run.py` | 57 | CLI with the three exit codes |

---

*Built as a portfolio sample. Deliberately dependency-free so it can be run and
audited in under a minute.*
