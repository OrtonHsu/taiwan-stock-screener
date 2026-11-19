import pytest

from daily_gap_audit import audit_rows, markdown_summary


DAYS = ["2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18"]
THRESHOLDS = {"TWSE": 2, "TPEX": 2}


def sample_rows():
    rows = []
    for day in DAYS:
        for code in ("1101", "1102", "1103"):
            # One missing quote, but other TWSE stocks satisfy daily threshold.
            if code == "1102" and day == "2026-11-17":
                continue
            rows.append((day, "TWSE", code))
        for code in ("6488", "6489"):
            rows.append((day, "TPEX", code))
    # A newly-listed/current stock must not be expected before its first quote.
    rows.append((DAYS[-1], "TWSE", "7777"))
    # Legacy duplicate-market spelling must never count as TPEX coverage.
    rows.append((DAYS[-1], "TPEx", "9999"))
    return rows


def test_finds_interior_gap_and_skips_weekend_and_new_listing():
    report = audit_rows(sample_rows(), thresholds=THRESHOLDS)
    assert report["latest_complete_date"] == "2026-11-18"
    assert report["complete_market_days"] == 4
    assert report["market_stats"]["TWSE"]["suspected_missing_stocks"] == 1
    assert report["suspected_stock_samples"] == [
        {"market": "TWSE", "code": "1102", "missing_count": 1,
         "sample_dates": ["2026-11-17"]}
    ]
    assert "2026-11-14" not in str(report)
    assert "7777" not in str(report)
    assert "9999" not in str(report)


def test_partial_market_day_not_treated_as_valid_comparison_day():
    rows = sample_rows()
    rows += [("2026-11-12", "TWSE", "1101"),
             ("2026-11-12", "TWSE", "1102")]
    report = audit_rows(rows, thresholds=THRESHOLDS)
    assert report["partial_market_days"] == 1
    assert report["partial_day_samples"][0] == {
        "date": "2026-11-12", "TWSE": 2, "TPEX": 0}
    assert report["market_stats"]["TWSE"]["suspected_missing_quotes"] == 1


def test_partial_day_after_latest_complete_day_is_reported():
    rows = sample_rows() + [("2026-11-19", "TWSE", "1101"),
                            ("2026-11-19", "TWSE", "1102")]
    report = audit_rows(rows, thresholds=THRESHOLDS)
    assert report["latest_complete_date"] == "2026-11-18"
    assert report["partial_market_days"] == 1
    assert report["partial_day_samples"][0]["date"] == "2026-11-19"


def test_both_markets_partial_day_is_detected():
    rows = sample_rows() + [("2026-11-12", "TWSE", "1101"),
                            ("2026-11-12", "TPEX", "6488")]
    report = audit_rows(rows, thresholds=THRESHOLDS)
    assert report["partial_market_days"] == 1
    assert report["partial_day_samples"][0] == {
        "date": "2026-11-12", "TWSE": 1, "TPEX": 1}


def test_no_complete_days_is_error():
    with pytest.raises(ValueError, match="上市及上櫃"):
        audit_rows([("2026-11-18", "TWSE", "1101")], thresholds=THRESHOLDS)


def test_markdown_states_read_only_and_limitations():
    report = audit_rows(sample_rows(), thresholds=THRESHOLDS)
    text = markdown_summary(report)
    assert "唯讀" in text
    assert "不會寫入" in text
    assert "1102" in text


from datetime import datetime
from zoneinfo import ZoneInfo
from tw_market_calendar import (
    UnsupportedCalendarYear, expected_market_dates, latest_due_market_date,
    market_is_open,
)


def dates_rows(days):
    return [(day, market, code)
            for day in days
            for market, codes in (("TWSE", ("1101", "1102")),
                                  ("TPEX", ("6488", "6489")))
            for code in codes]


def test_official_closures_and_typhoon_do_not_count_as_missing():
    assert expected_market_dates("2026-10-22", "2026-10-27") == [
        "2026-10-22", "2026-10-27"
    ]
    assert not market_is_open("2026-10-23")  # Confirmed typhoon shutdown.
    assert not market_is_open("2026-10-26")  # Annual holiday.
    assert expected_market_dates("2026-09-24", "2026-09-29") == [
        "2026-09-24", "2026-09-29"
    ]
    report = audit_rows(
        dates_rows(["2026-10-22", "2026-10-27"]), thresholds=THRESHOLDS,
        official_dates=expected_market_dates("2026-10-22", "2026-10-27")
    )
    assert report["official_calendar"]["fully_missing_market_days"] == 0
    assert report["official_calendar"]["quotes_on_closed_days"] == 0


def test_identifies_entire_market_missing_date():
    days = ["2026-10-21", "2026-10-27"]
    report = audit_rows(
        dates_rows(days), thresholds=THRESHOLDS,
        official_dates=expected_market_dates("2026-10-21", "2026-10-27")
    )
    assert report["official_calendar"]["fully_missing_date_samples"] == [
        "2026-10-22"
    ]
    assert "2026-10-23" not in str(report["official_calendar"])
    assert report["official_calendar"]["fully_missing_market_days"] == 1
    assert "整日" in markdown_summary(report)


def test_flags_quotes_on_confirmed_closed_day_without_using_it_as_benchmark():
    days = ["2026-10-22", "2026-10-23", "2026-10-27"]
    report = audit_rows(
        dates_rows(days), thresholds=THRESHOLDS,
        official_dates=expected_market_dates("2026-10-22", "2026-10-27")
    )
    assert report["complete_market_days"] == 2
    assert report["official_calendar"]["closed_day_quote_samples"] == [
        "2026-10-23"
    ]


def test_calendar_cutoff_only_after_16_00_taipei():
    local = ZoneInfo("Asia/Taipei")
    assert latest_due_market_date(
        datetime(2026, 11, 19, 10, 0, tzinfo=local)
    ) == "2026-11-18"
    assert latest_due_market_date(
        datetime(2026, 11, 19, 17, 0, tzinfo=local)
    ) == "2026-11-19"
    assert latest_due_market_date(
        datetime(2026, 10, 26, 18, 0, tzinfo=local)
    ) == "2026-10-22"


def test_unsupported_year_is_not_assumed_to_be_a_trading_day():
    with pytest.raises(UnsupportedCalendarYear):
        market_is_open("2027-01-04")
    with pytest.raises(UnsupportedCalendarYear):
        expected_market_dates("2026-12-01", "2027-01-05")
