"""Safety and point-in-time tests for close-only entry-timing labels."""

import pandas as pd
import pytest

from entry_timing import ENTRY_COLUMNS, evaluate_entry_timing


def sample_prices(last=None, *, code="2330", last_volume=2500):
    days = pd.bdate_range("2026-06-01", periods=75).strftime("%Y-%m-%d").tolist()
    closes = [95 + 0.12 * i for i in range(75)]
    if last is not None:
        closes[-1] = last
    rows = [
        {
            "date": day, "market": "TWSE", "code": code, "name": "測試股票",
            "close": closes[i], "volume": last_volume if i == 74 else 1000,
            "amount": 80_000_000,
        }
        for i, day in enumerate(days)
    ]
    return pd.DataFrame(rows), days


def test_breakout_excludes_today_from_reference_and_volume_baseline():
    prices, dates = sample_prices(last=105.0)
    result = evaluate_entry_timing(prices, dates)
    assert len(result) == 1
    r = result.iloc[0]
    assert r["stage"] == "收盤突破觀察"
    assert r["date"] == dates[-1]
    assert r["prior20_high"] == pytest.approx(round(95 + 0.12 * 73, 2))
    assert r["volume_ratio"] == 2.5
    assert r["reference_price"] == r["prior20_high"]
    assert r["support_reference"] < r["close"]
    assert "長期資料仍待驗收" in r["data_quality"]


def test_same_data_with_future_day_does_not_change_asof_result():
    prices, dates = sample_prices(last=105.0)
    before = evaluate_entry_timing(prices, dates)
    later = pd.DataFrame([{
        "date": "2027-01-01", "market": "TWSE", "code": "2330",
        "name": "未來", "close": 9999, "volume": 9_999_999,
        "amount": 9_999_999,
    }])
    after = evaluate_entry_timing(pd.concat([prices, later], ignore_index=True), dates)
    pd.testing.assert_frame_equal(before, after)


def test_risky_extension_not_mislabeled_as_entry_confirmation():
    prices, dates = sample_prices(last=130.0)
    result = evaluate_entry_timing(prices, dates)
    assert len(result) == 1
    assert result.iloc[0]["stage"] == "追價風險偏高"
    assert result.iloc[0]["extension_pct"] > 8


def test_missing_one_recent_complete_market_day_excluded():
    prices, dates = sample_prices(last=105.0)
    gap = dates[-7]
    result = evaluate_entry_timing(prices.loc[prices["date"] != gap], dates)
    assert result.empty


def test_stock_without_signal_day_quote_excluded():
    prices, dates = sample_prices(last=105.0)
    assert evaluate_entry_timing(prices.iloc[:-1], dates).empty


def test_insufficient_verified_market_dates_suppresses_signals():
    prices, dates = sample_prices(last=105.0)
    result = evaluate_entry_timing(prices, dates[-25:])
    assert result.empty and list(result.columns) == ENTRY_COLUMNS


def test_invalid_inputs_and_no_histories():
    prices, dates = sample_prices(last=105.0)
    with pytest.raises(ValueError, match="65"):
        evaluate_entry_timing(prices, dates, min_history=60)
    with pytest.raises(ValueError, match="成交金額"):
        evaluate_entry_timing(prices, dates, min_amount=-1)
    with pytest.raises(ValueError, match="行情欄位"):
        evaluate_entry_timing(prices.drop(columns=["volume"]), dates)
    assert evaluate_entry_timing(prices.iloc[:0], dates).empty


def test_failed_turnover_and_invalid_volume_do_not_create_signal():
    prices, dates = sample_prices(last=105.0)
    prices["amount"] = 1000
    assert evaluate_entry_timing(prices, dates).empty
    prices["amount"] = 80_000_000
    prices.loc[prices["date"] == dates[-1], "volume"] = 0
    assert evaluate_entry_timing(prices, dates).empty


def test_prebreakout_reference_and_pullback_stages_are_separate():
    prices, dates = sample_prices()
    # A genuine close-based turn off the MA20: prior close was down,
    # current close rebounds toward the 5-session average.
    prices.loc[prices.date == dates[-3], "close"] = 103.7
    prices.loc[prices.date == dates[-2], "close"] = 102.8
    prices.loc[prices.date == dates[-1], "close"] = 103.9
    prices.loc[prices.date == dates[-1], "volume"] = 900
    stage = evaluate_entry_timing(prices, dates).iloc[0]["stage"]
    assert stage == "回測初步止跌"

    prices.loc[prices.date == dates[-1], "close"] = 102.4
    stage = evaluate_entry_timing(prices, dates).iloc[0]["stage"]
    assert stage == "等待止跌確認"


def test_market_and_code_validation_does_not_mix_in_legacy_quotes():
    prices, dates = sample_prices(last=105.0)
    copies = prices.copy()
    copies["market"] = "TPEx"
    mixed = pd.concat([prices, copies], ignore_index=True)
    output = evaluate_entry_timing(mixed, dates)
    assert set(output.market) == {"TWSE"}
