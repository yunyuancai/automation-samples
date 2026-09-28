"""Tests for the cleaning pipeline.

    python test_pipeline.py

(pytest also works: `pytest test_pipeline.py`.)
"""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

from pipeline import (
    FatalError,
    Pipeline,
    Report,
    clean_amount,
    clean_country,
    clean_date,
    clean_email,
    clean_order_id,
)
import run as run_module


# -- amount ----------------------------------------------------------------


def test_amount_plain():
    assert clean_amount("89.99") == (True, "89.99", "")


def test_amount_strips_currency_and_thousands():
    assert clean_amount("$1,234.50") == (True, "1234.50", "")


def test_amount_tolerates_whitespace():
    assert clean_amount("  $ 42.00 ") == (True, "42.00", "")


def test_amount_zero_is_valid():
    ok, value, _ = clean_amount("0")
    assert ok and value == "0.00"


def test_amount_rejects_missing_tokens():
    for junk in ("", "  ", "N/A", "null", "-", "TBD"):
        ok, _, err = clean_amount(junk)
        assert not ok and err == "amount_missing", junk


def test_amount_rejects_negative():
    ok, _, err = clean_amount("-5.00")
    assert not ok and err == "amount_negative"


def test_amount_rejects_eu_style_as_ambiguous():
    """'1.234,50' could be 1234.50 or 1.23450 — refuse rather than guess."""
    ok, _, err = clean_amount("1.234,50")
    assert not ok and err == "amount_ambiguous_decimal_separator"


def test_amount_rejects_bare_comma_decimal():
    ok, _, err = clean_amount("1234,50")
    assert not ok and err == "amount_ambiguous_decimal_separator"


def test_amount_rejects_garbage():
    ok, _, err = clean_amount("about ten dollars")
    assert not ok and err.startswith("amount_")


# -- email -----------------------------------------------------------------


def test_email_normalises_case_and_space():
    assert clean_email("  MESSY@Example.COM ") == (True, "messy@example.com", "")


def test_email_rejects_invalid():
    ok, _, err = clean_email("not-an-email")
    assert not ok and err == "email_invalid"


def test_email_rejects_missing():
    ok, _, err = clean_email("")
    assert not ok and err == "email_missing"


# -- order id --------------------------------------------------------------


def test_order_id_normalises():
    assert clean_order_id(" ab-1012 ") == (True, "AB-1012", "")


def test_order_id_rejects_bad_format():
    ok, _, err = clean_order_id("BADID")
    assert not ok and err == "order_id_bad_format"


# -- date ------------------------------------------------------------------


def test_date_iso_passthrough():
    assert clean_date("2026-01-15") == (True, "2026-01-15", "")


def test_date_compact_form():
    assert clean_date("20260411") == (True, "2026-04-11", "")


def test_date_unambiguous_slash_accepted():
    # 15 cannot be a month, so this is unambiguous and must parse.
    ok, value, _ = clean_date("15/01/2026")
    assert ok and value == "2026-01-15"


def test_date_ambiguous_rejected():
    """The core promise: do not guess which number is the month."""
    ok, _, err = clean_date("05/03/2026")
    assert not ok and err == "date_ambiguous_day_month_order"


def test_date_same_number_is_not_ambiguous():
    # 03/03/2026 is the same day either way, so it is safe to accept.
    ok, value, _ = clean_date("03/03/2026")
    assert ok and value == "2026-03-03"


def test_date_unparsable():
    ok, _, err = clean_date("not a date")
    assert not ok and err == "date_unparsable:not a date"


# -- country ---------------------------------------------------------------


def test_country_alias_uk_maps_to_gb():
    """UK is not an ISO code; GB is. Normalise rather than reject."""
    assert clean_country("uk") == (True, "GB", "")


def test_country_alias_germany():
    assert clean_country("Germany") == (True, "DE", "")


def test_country_cjk_alias():
    assert clean_country("中国") == (True, "CN", "")


def test_country_unrecognised():
    ok, _, err = clean_country("Wakanda")
    assert not ok and err.startswith("country_unrecognised")


# -- pipeline behaviour ----------------------------------------------------


def _rows() -> list[dict]:
    return [
        {"order_id": "AB-1001", "email": "a@example.com", "order_date": "2026-01-15",
         "amount": "$1,234.50", "country": "US", "note": "ok"},
        {"order_id": "AB-1002", "email": "b@example.com", "order_date": "05/03/2026",
         "amount": "10.00", "country": "US", "note": "ambiguous date"},
        {"order_id": "AB-1003", "email": "bad", "order_date": "2026-02-02",
         "amount": "10.00", "country": "US", "note": "bad email"},
        {"order_id": "AB-1001", "email": "a@example.com", "order_date": "2026-01-15",
         "amount": "1.00", "country": "US", "note": "duplicate id"},
    ]


def test_pipeline_splits_accepted_and_rejected():
    result = Pipeline().run(_rows())
    assert result.report.total == 4
    assert result.report.accepted == 1          # only the first row is clean
    assert result.report.rejected == 3
    assert len(result.accepted) == 1
    assert result.accepted[0]["amount"] == "1234.50"


def test_pipeline_counts_duplicates_separately():
    result = Pipeline().run(_rows())
    assert result.report.duplicates == 1


def test_pipeline_records_actionable_reasons():
    result = Pipeline().run(_rows())
    reasons = result.report.reason_counts
    assert "date_ambiguous_day_month_order" in reasons
    assert "email_invalid" in reasons
    assert "duplicate_order_id" in reasons


def test_rejected_rows_keep_original_data():
    result = Pipeline().run(_rows())
    rej = {r.row_number: r for r in result.rejected}
    # Row numbers are 1-based including the header, so the first data row is 2.
    assert rej[3].raw["order_date"] == "05/03/2026"       # original retained
    assert rej[3].reasons == ["date_ambiguous_day_month_order"]


def test_validate_header_raises_fatal_on_missing_column():
    try:
        Pipeline().validate_header(["order_id", "email"])
    except FatalError as exc:
        assert "order_date" in str(exc) and "amount" in str(exc)
    else:
        raise AssertionError("expected FatalError")


def test_validate_header_accepts_extra_columns():
    Pipeline().validate_header(
        ["order_id", "email", "order_date", "amount", "country", "note", "extra"]
    )


# -- report ----------------------------------------------------------------


def test_report_accept_rate():
    r = Report(total=4, accepted=1, rejected=3)
    assert r.accept_rate == 0.25


def test_report_accept_rate_handles_zero_rows():
    assert Report().accept_rate == 0.0


def test_report_render_mentions_fatal_and_writes_nothing():
    text = Report(fatal="missing required column(s): amount").render()
    assert "FATAL" in text and "No output written" in text


def test_report_render_lists_reasons_most_common_first():
    r = Report(total=10, accepted=2, rejected=8,
               reason_counts={"email_invalid": 7, "amount_negative": 1})
    text = r.render()
    assert text.index("email_invalid") < text.index("amount_negative")


# -- end to end ------------------------------------------------------------


def test_run_file_writes_all_four_outputs():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "in.csv"
        with src.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(
                fh, fieldnames=["order_id", "email", "order_date", "amount", "country", "note"]
            )
            w.writeheader()
            w.writerows(_rows())

        outdir = tmp_path / "out"
        result = Pipeline().run_file(src, outdir)

        for name in ("orders_clean.csv", "orders_rejected.csv", "report.json", "report.txt"):
            assert (outdir / name).exists(), name

        payload = json.loads((outdir / "report.json").read_text(encoding="utf-8"))
        assert payload["rows_accepted"] == 1
        assert len(payload["input_sha256"]) == 64

        with (outdir / "orders_rejected.csv").open(encoding="utf-8") as fh:
            rejected = list(csv.DictReader(fh))
        assert len(rejected) == 3
        assert all(r["_reasons"] for r in rejected)


def test_missing_input_file_is_fatal():
    try:
        Pipeline().run_file("does-not-exist.csv", "out")
    except FatalError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected FatalError")


# -- CLI exit codes --------------------------------------------------------


def test_cli_returns_2_on_fatal():
    assert run_module.main(["--input", "nope-missing.csv", "--output", "out"]) == 2


def test_cli_returns_3_when_reject_rate_exceeds_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.csv"
        with src.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(
                fh, fieldnames=["order_id", "email", "order_date", "amount", "country", "note"]
            )
            w.writeheader()
            w.writerows(_rows())          # 3 of 4 rows rejected = 75%
        rc = run_module.main(
            ["--input", str(src), "--output", str(Path(tmp) / "out"),
             "--max-reject-rate", "0.25"]
        )
        assert rc == 3


def test_cli_returns_0_on_clean_input():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.csv"
        with src.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(
                fh, fieldnames=["order_id", "email", "order_date", "amount", "country", "note"]
            )
            w.writeheader()
            w.writerow(
                {"order_id": "AB-1001", "email": "a@example.com",
                 "order_date": "2026-01-15", "amount": "10.00", "country": "US", "note": ""}
            )
        rc = run_module.main(
            ["--input", str(src), "--output", str(Path(tmp) / "out")]
        )
        assert rc == 0


# -- runner ----------------------------------------------------------------


def _run_all() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed.append((name, f"AssertionError: {exc}"))
        except Exception as exc:  # noqa: BLE001
            failed.append((name, f"{type(exc).__name__}: {exc}"))
        else:
            passed += 1
            print(f"  PASS  {name}")

    print()
    for name, err in failed:
        print(f"  FAIL  {name}\n        {err}")
    print(f"\n{passed} passed, {len(failed)} failed, {len(tests)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
