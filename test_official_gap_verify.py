"""Offline checks for the manual-only individual official quote audit."""

from datetime import date

import pytest

from official_gap_verify import (
    choose_batches, compare_official_month, compare_batches, markdown_report,
    raw_official_dates, VerifiedOfficialMonth, fetch_verified_month,
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



def test_original_tpex_table_dates_include_rows_without_close():
    payload = {
        "stat": "ok",
        "tables": [{"fields": ["日 期", "成交張數", "成交仟元", "收盤"],
                    "data": [
                        ["115/11/16", "0", "0", "--"],
                        ["115/11/17*", "5", "100", "35.1"],
                    ]}],
    }
    assert raw_official_dates("TPEX", payload, "2026-11") == {
        "2026-11-16", "2026-11-17"
    }


def test_original_twse_table_includes_no_price_date():
    payload = {
        "stat": "OK", "fields": ["日期", "收盤價"],
        "data": [["115/11/16", "--"], ["115/11/17", "42.0"]],
    }
    assert raw_official_dates("TWSE", payload, "2026-11") == {
        "2026-11-16", "2026-11-17"
    }


def test_exact_three_way_classification_of_suspected_dates():
    batch = {
        "market": "TPEX", "code": "6488", "month": "2026-11",
        "suspected_dates": ["2026-11-16", "2026-11-17", "2026-11-18"],
    }
    rows = [
        ("2026-11-16", "TPEX", "6488", "測試", 100.0, 1000, 100000),
    ]
    result = compare_official_month(
        batch, rows, {"2026-11-16", "2026-11-17"}
    )
    assert result["confirmed_missing_quote_dates"] == ["2026-11-16"]
    assert result["official_listed_no_valid_close_dates"] == ["2026-11-17"]
    assert result["official_date_not_listed_dates"] == ["2026-11-18"]
    assert result["unresolved_dates"] == ["2026-11-17", "2026-11-18"]


def test_no_raw_table_does_not_falsely_classify_no_price_dates():
    batch = {
        "market": "TWSE", "code": "2321", "month": "2026-11",
        "suspected_dates": ["2026-11-16"],
    }
    result = compare_official_month(batch, [])
    assert result["unresolved_dates"] == ["2026-11-16"]
    assert result["official_listed_no_valid_close_dates"] == []
    assert result["official_date_not_listed_dates"] == []


def test_valid_parsed_date_must_also_exist_in_original_table():
    batch = {"market": "TWSE", "code": "1102", "month": "2026-11",
             "suspected_dates": ["2026-11-16"]}
    row = ("2026-11-16", "TWSE", "1102", "測試", 100, 1000, 1000)
    with pytest.raises(ValueError, match="原始表格"):
        compare_official_month(batch, [row], {"2026-11-17"})


def test_original_table_rejects_duplicate_wrong_month_and_bad_fields():
    duplicate = {
        "stat": "OK", "fields": ["日期", "收盤價"],
        "data": [["115/11/16", "25"], ["115/11/16", "--"]],
    }
    with pytest.raises(ValueError, match="重複"):
        raw_official_dates("TWSE", duplicate, "2026-11")
    with pytest.raises(ValueError, match="日期不屬於"):
        raw_official_dates(
            "TWSE",
            {"stat": "OK", "fields": ["日期"], "data": [["115/10/16"]]},
            "2026-11",
        )
    with pytest.raises(ValueError, match="日期欄位"):
        raw_official_dates("TWSE", {"stat": "OK", "fields": [],
                                    "data": []}, "2026-11")


def test_live_fetch_wrapper_returns_dates_from_same_official_payload(monkeypatch):
    import official_gap_verify as mod

    payload = {
        "stat": "OK",
        "fields": ["日期", "成交股數", "成交金額", "收盤價"],
        "data": [
            ["115/11/16", "0", "0", "--"],
            ["115/11/17", "1,000", "10,000", "10"],
        ],
    }
    calls = []
    def fake_fetch(session, code, month):
        calls.append((code, month))
        return payload
    monkeypatch.setattr(mod, "get_twse_month", fake_fetch)
    result = fetch_verified_month(None, "TWSE", "1102", date(2026, 11, 1))
    assert isinstance(result, VerifiedOfficialMonth)
    assert len(result.valid_rows) == 1
    assert result.raw_dates == {"2026-11-16", "2026-11-17"}
    assert calls == [("1102", date(2026, 11, 1))]


def test_report_contains_both_new_uncertainty_categories():
    batch = {
        "market": "TPEX", "code": "6488", "month": "2026-11",
        "suspected_dates": ["2026-11-16", "2026-11-17"],
    }

    def mock_fetch(*args):
        return VerifiedOfficialMonth(
            [], {"2026-11-16"}
        )

    results, errors = compare_batches([batch], mock_fetch)
    assert errors == []
    assert results[0]["official_listed_no_valid_close_dates"] == [
        "2026-11-16"
    ]
    assert results[0]["official_date_not_listed_dates"] == [
        "2026-11-17"
    ]
    report = {
        "latest_complete_date": "2026-11-18",
        "checked_groups": 1, "failed_groups": 0,
        "confirmed_missing_quotes": 0, "unresolved_quotes": 2,
        "official_listed_no_valid_close": 1, "official_date_not_listed": 1,
        "results": results, "errors": [],
    }
    markdown = markdown_report(report)
    assert "官方有列日期但無有效收盤價" in markdown
    assert "官方未列日期" in markdown
    assert "2026-11-16" in markdown
    assert "2026-11-17" in markdown
    assert "不修改 Supabase" in markdown
