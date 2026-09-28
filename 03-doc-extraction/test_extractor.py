"""Tests for the parameter-table extractor.

    python test_extractor.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from extractor import (
    FatalError,
    Param,
    detect_collisions,
    dedupe_keep_first,
    extract,
    parse_line,
    parse_number,
    parse_range,
    render_report,
    write_outputs,
)
import run as run_module

SAMPLES = Path(__file__).parent / "samples"


# -- number parsing --------------------------------------------------------


def test_bare_number_is_decimal():
    """A bare number must NOT be guessed as hex from its digits."""
    assert parse_number("10") == (True, 10, "dec", "")


def test_h_suffix_is_hex():
    assert parse_number("10h") == (True, 16, "hex", "")


def test_hex_suffix_on_sixteen():
    assert parse_number("0010h") == (True, 16, "hex", "")


def test_number_empty():
    ok, _, _, err = parse_number("")
    assert not ok and err == "value_empty"


def test_number_garbage():
    ok, _, _, err = parse_number("abc!")
    assert not ok and err.startswith("value_unparsable")


# -- range parsing ---------------------------------------------------------


def test_range_hex():
    assert parse_range("1000-1265h") == (True, 0x1000, 0x1265, "hex", "")


def test_range_decimal():
    assert parse_range("0-100") == (True, 0, 100, "dec", "")


def test_range_absent_variants():
    for raw in ("", "-", "n/a", "N/A"):
        assert parse_range(raw) == (True, None, None, "", "")


def test_range_mixed_radix_rejected():
    """'0000-FFFF' has hex digits but no suffix — the intent is unclear."""
    ok, _, _, _, err = parse_range("0000-FFFF")
    assert not ok and err.startswith("range_mixed_radix")


def test_range_inverted_rejected():
    ok, _, _, _, err = parse_range("0100-0050h")
    assert not ok and err.startswith("range_inverted")


def test_range_garbage_rejected():
    ok, _, _, _, err = parse_range("wide")
    assert not ok and err.startswith("range_unparsable")


# -- line parsing ----------------------------------------------------------


def test_parse_valid_line():
    ok, param, _ = parse_line(
        "PA01 | *STY | Operation mode | - | 1000-1265h | 1000h", "a.txt", 3
    )
    assert ok and param is not None
    assert param.id == "PA01"
    assert param.group == "PA"
    assert param.minimum == 0x1000
    assert param.maximum == 0x1265
    assert param.radix == "hex"
    assert param.default == 0x1000
    assert param.unit == ""          # "-" normalises to empty
    assert param.source == "a.txt"
    assert param.line == 3


def test_parse_keeps_unit_when_present():
    ok, param, _ = parse_line("PS01 | *SSF | Speed limit | r/min | 0-100 | 50")
    assert ok and param is not None
    assert param.unit == "r/min" and param.radix == "dec"


def test_blank_and_comment_are_not_failures():
    assert parse_line("")[2] == "blank"
    assert parse_line("   ")[2] == "blank"
    assert parse_line("# a comment")[2] == "comment"


def test_wrong_field_count_rejected():
    ok, _, err = parse_line("PA07 | *BAD | Missing fields | - | 0-100")
    assert not ok and err.startswith("wrong_field_count")


def test_bad_parameter_id_rejected():
    ok, _, err = parse_line("XX01 | *SYM | Bad id | - | 0-100 | 0")
    assert not ok and err.startswith("bad_parameter_id")


def test_default_out_of_range_rejected():
    ok, _, err = parse_line("PA09 | *OOR | Out of range | - | 0000-0005h | 0009h")
    assert not ok and err.startswith("default_out_of_range")


def test_default_radix_mismatch_rejected():
    ok, _, err = parse_line("PA10 | *RAD | Radix clash | - | 0000-0005h | 3")
    assert not ok and err.startswith("default_radix_mismatch")


def test_empty_name_rejected():
    ok, _, err = parse_line("PA12 | *NUL |  | - | 0-100 | 0")
    assert not ok and err == "name_empty"


# -- collisions ------------------------------------------------------------


def _p(pid: str, *, unit: str = "", lo: int = 0, hi: int = 1,
       default: int = 0, source: str = "s.txt") -> Param:
    return Param(
        id=pid, group=pid[:2], symbol="*X", name="n", unit=unit,
        range_raw="0-1", minimum=lo, maximum=hi, radix="dec",
        default_raw="0", default=default, source=source, line=1,
    )


def test_no_collision_for_identical_definitions():
    assert detect_collisions([_p("PA01"), _p("PA01", source="other.txt")]) == []


def test_collision_on_differing_range():
    cols = detect_collisions([_p("PA01", hi=3), _p("PA01", hi=7, source="b.txt")])
    assert len(cols) == 1
    assert "maximum" in cols[0].differing_fields
    assert len(cols[0].variants) == 2


def test_collision_reports_multiple_differing_fields():
    cols = detect_collisions(
        [_p("PS01", unit="r/min", hi=100, default=50),
         _p("PS01", unit="rpm", hi=120, default=60, source="b.txt")]
    )
    assert len(cols) == 1
    assert set(cols[0].differing_fields) >= {"unit", "maximum", "default"}


def test_single_definition_is_not_a_collision():
    assert detect_collisions([_p("PA01"), _p("PA02")]) == []


def test_dedupe_keeps_first():
    kept = dedupe_keep_first([_p("PA01", hi=3), _p("PA01", hi=7, source="b.txt")])
    assert len(kept) == 1
    assert kept[0].maximum == 3


# -- end to end ------------------------------------------------------------


def test_extract_sample_parses_expected_counts():
    result = extract([SAMPLES / "mr_j4a.txt"])
    ids = {p.id for p in result.params}
    assert "PA01" in ids and "PS01" in ids
    # 25 data lines, 6 of which are deliberately malformed.
    assert len(result.params) == 19
    assert len(result.unparsed) == 6
    assert result.collisions == []


def test_extract_records_all_six_rejection_reasons():
    result = extract([SAMPLES / "mr_j4a.txt"])
    reasons = {u.reason.split(":")[0] for u in result.unparsed}
    assert reasons == {
        "wrong_field_count",
        "range_mixed_radix",
        "bad_parameter_id",
        "default_out_of_range",
        "default_radix_mismatch",
        "range_inverted",
    }


def test_extract_detects_cross_source_collisions():
    result = extract([SAMPLES / "mr_j4a.txt", SAMPLES / "mr_j4a_rev2.txt"])
    collided = {c.param_id for c in result.collisions}
    assert collided == {"PA02", "PS01"}
    ps01 = next(c for c in result.collisions if c.param_id == "PS01")
    assert set(ps01.differing_fields) >= {"unit", "maximum", "default"}


def test_extract_keeps_source_and_line_for_traceability():
    result = extract([SAMPLES / "mr_j4a.txt"])
    pa01 = next(p for p in result.params if p.id == "PA01")
    assert pa01.source == "mr_j4a.txt"
    assert pa01.line == 4          # after the three comment lines


def test_extract_rejects_no_input():
    try:
        extract([])
    except FatalError as exc:
        assert "no input" in str(exc)
    else:
        raise AssertionError("expected FatalError")


def test_extract_missing_file_is_fatal():
    try:
        extract(["nope-does-not-exist.txt"])
    except FatalError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected FatalError")


def test_extract_all_unparsable_is_fatal():
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.txt"
        bad.write_text("this is not a parameter table\nnor is this\n", encoding="utf-8")
        try:
            extract([bad])
        except FatalError as exc:
            assert "no parameters parsed" in str(exc)
        else:
            raise AssertionError("expected FatalError")


def test_write_outputs_produces_all_four_files():
    with tempfile.TemporaryDirectory() as tmp:
        result = extract([SAMPLES / "mr_j4a.txt"])
        write_outputs(result, tmp)
        for name in ("params.json", "params.csv", "unparsed.txt", "report.txt"):
            assert (Path(tmp) / name).exists(), name

        payload = json.loads((Path(tmp) / "params.json").read_text(encoding="utf-8"))
        assert payload["counts"]["parsed"] == 19
        assert payload["counts"]["unparsed"] == 6
        assert len(payload["params"]) == 19

        unparsed = (Path(tmp) / "unparsed.txt").read_text(encoding="utf-8")
        assert "default_out_of_range" in unparsed


def test_report_names_the_collision_pairs():
    result = extract([SAMPLES / "mr_j4a.txt", SAMPLES / "mr_j4a_rev2.txt"])
    text = render_report(result)
    assert "COLLISIONS" in text
    assert "PA02" in text and "PS01" in text
    assert "do not let load order decide" in text.lower()


def test_report_says_unparsed_were_not_dropped():
    result = extract([SAMPLES / "mr_j4a.txt"])
    assert "NOT silently dropped" in render_report(result)


# -- CLI exit codes --------------------------------------------------------


def test_cli_returns_0_on_samples():
    with tempfile.TemporaryDirectory() as tmp:
        rc = run_module.main(
            ["--input", str(SAMPLES / "mr_j4a.txt"), "--output", tmp]
        )
        assert rc == 0


def test_cli_returns_2_with_strict_and_unparsed_lines():
    with tempfile.TemporaryDirectory() as tmp:
        rc = run_module.main(
            ["--input", str(SAMPLES / "mr_j4a.txt"), "--output", tmp, "--strict"]
        )
        assert rc == 2


def test_cli_returns_4_on_collision_when_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        rc = run_module.main(
            ["--input", str(SAMPLES / "mr_j4a.txt"),
             "--input", str(SAMPLES / "mr_j4a_rev2.txt"),
             "--output", tmp, "--fail-on-collision"]
        )
        assert rc == 4


def test_cli_returns_0_on_collision_without_flag():
    with tempfile.TemporaryDirectory() as tmp:
        rc = run_module.main(
            ["--input", str(SAMPLES / "mr_j4a.txt"),
             "--input", str(SAMPLES / "mr_j4a_rev2.txt"),
             "--output", tmp]
        )
        assert rc == 0


def test_cli_returns_2_on_missing_input():
    assert run_module.main(["--input", "definitely-missing.txt", "--output", "out"]) == 2


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
