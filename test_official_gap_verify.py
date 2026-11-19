"""Offline checks for the manual-only individual official quote audit."""

from datetime import date

import pytest

from official_gap_verify import (
    choose_batches, compare_official_month, compare_batches, markdown_report,
)


DAYS = ["2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18"]
LIMITS = {"TWSE": 2, "TPEX": 2}


def sample_rows():
    rows = []
    for day in DAYS:
        for code in ("1101", "1102", "1103"):
            if code == "1102" and day == "2026-11-16":
                continue
            rows.append((day, "TWSE", code))
        for code in ("6488", "6489", "6490"):
            if code == "6488" and day == "2026-11-17":
                continue
            rows.append((day, "TPEX", code))
    rows += [
        ("2026-11-17", "TPEx", "9999"),  # legacy market never counts
        ("2026-11-18", "TWSE", "7777"),  # new listing no prelisting gaps
    ]
    return rows


def test_selects_listed_and_otc_fairly():
    latest, selected = choose_batches(
        sample_rows(), DAYS, max_requests=2, thresholds=LIMITS
    )
    assert latest == "2026-11-18"
    assert [(s["market"], s["code"], s["month"]) for s in selected] == [
        ("TPEX", "6488", "2026-11"),
        ("TWSE", "1102", "2026-11"),
    ]
    assert selected[0]["suspected_dates"] == ["2026-11-17"]
    assert selected[1]["suspected_dates"] == ["2026-11-16"]


def test_ignores_closed_days_in_official_comparison_dates():
    source = sample_rows()
    # Even if database accidentally has quotes on this official closed day,
    # the selector must not use it to infer other stock gaps.
    source += [
        ("2026-11-14", "TWSE", "1101"),
        ("2026-11-14", "TWSE", "1102"),
        ("2026-11-14", "TPEX", "6488"),
        ("2026-11-14", "TPEX", "6489"),
    ]
    _, selected = choose_batches(
        source, DAYS, max_requests=3, thresholds=LIMITS
    )
    assert all(
        "2026-11-14" not in s["suspected_dates"] for s in selected
    )


def test_identifies_only_dates_with_verified_valid_close():
    batch = {"market": "TPEX", "code": "6488", "month": "2026-11",
             "suspected_dates": ["2026-11-16", "2026-11-17"]}
    rows = [
        ("2026-11-16", "TPEX", "6488", "測試", 100.0, 1000.0, 100000.0),
        ("2026-11-18", "TPEX", "6488", "測試", 101.0, 1000.0, 101000.0),
    ]
    result = compare_official_month(batch, rows)
    assert result["confirmed_missing_quote_dates"] == ["2026-11-16"]
    assert result["unresolved_dates"] == ["2026-11-17"]
    assert result["official_valid_quotes"] == 2


@pytest.mark.parametrize("row", [
    ("2026-11-16", "TWSE", "6488", "測試", 100, 1000, 1000),
    ("2026-10-16", "TPEX", "6488", "測試", 100, 1000, 1000),
    ("2026-11-16", "TPEX", "6489", "測試", 100, 1000, 1000),
])
def test_rejects_wrong_market_month_or_code(row):
    batch = {"market": "TPEX", "code": "6488", "month": "2026-11",
             "suspected_dates": ["2026-11-16"]}
    with pytest.raises(ValueError, match="不符合"):
        compare_official_month(batch, [row])


def test_rejects_duplicate_official_date():
    batch = {"market": "TWSE", "code": "1102", "month": "2026-11",
             "suspected_dates": ["2026-11-16"]}
    row = ("2026-11-16", "TWSE", "1102", "測試", 100, 1000, 1000)
    with pytest.raises(ValueError, match="重複"):
        compare_official_month(batch, [row, row])


def test_failed_source_stays_unverified_not_proven_missing():
    batch = {"market": "TWSE", "code": "1102", "month": "2026-11",
             "suspected_dates": ["2026-11-16"]}

    def bad_fetch(*args):
        raise RuntimeError("來源暫時無法取得")

    results, errors = compare_batches([batch], bad_fetch)
    assert not results
    assert len(errors) == 1
    assert "來源暫時無法取得" in errors[0]


def test_validated_results_render_read_only_summary():
    report = {
        "latest_complete_date": "2026-11-18",
        "checked_groups": 1, "failed_groups": 0,
        "confirmed_missing_quotes": 1, "unresolved_quotes": 1,
        "results": [{
            "market": "TPEX", "code": "6488", "month": "2026-11",
            "confirmed_missing_quote_dates": ["2026-11-16"],
            "unresolved_dates": ["2026-11-17"],
        }],
        "errors": [],
    }
    out = markdown_report(report)
    assert "唯讀" in out
    assert "不修改 Supabase" in out
    assert "2026-11-16" in out
    assert "2026-11-17" not in out  # no false claim of verified no-trade


def test_request_bounds_and_no_complete_days_fail_closed():
    with pytest.raises(ValueError, match="max_requests"):
        choose_batches(sample_rows(), DAYS, max_requests=11, thresholds=LIMITS)
    with pytest.raises(ValueError, match="安全門檻"):
        choose_batches([], DAYS, max_requests=2, thresholds=LIMITS)
