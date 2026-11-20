"""Point-in-time, data-gap and cohort tests for V2.2 read-only research."""

from datetime import date

import pandas as pd
import pytest

from v22_signal_validation import (
    HORIZONS, PROSPECTIVE_FROM, SIGNAL_COLUMNS, HORIZON_COLUMNS,
    check_next_close, evaluate_recorded_signals, summarize_matured,
)


def fixture():
    dates = pd.bdate_range("2026-11-02", periods=65).strftime("%Y-%m-%d").tolist()
    start = 18  # After prospective cutoff.
    closes = [100 + 0.10 * i for i in range(len(dates))]
    closes[start] = 106.0
    closes[start + 1] = 105.0
    closes[start + 2] = 105.5
    records = pd.DataFrame([
        {"date": day, "market": "TWSE", "code": "2330",
         "name": "測試", "close": closes[i]}
        for i, day in enumerate(dates)
    ])
    positions = pd.DataFrame([{
        "market": "TWSE", "code": "2330", "name": "測試",
        "setup": "收盤突破觀察", "signal_date": dates[start],
        "signal_close": 106.0, "invalidation_reference": 104.0,
        "entry_date": dates[start + 1], "entry_close": 105.0,
    }])
    return positions, records, dates, start


def test_t1_confirmation_never_claims_same_day_execution():
    positions, prices, dates, i = fixture()
    signals, h = evaluate_recorded_signals(positions, prices, dates)
    row = signals.iloc[0]
    assert list(signals.columns) == SIGNAL_COLUMNS
    assert list(h.columns) == HORIZON_COLUMNS
    assert row["cohort"] == "前瞻樣本"
    assert row["check_status"] == "通過"
    assert row["t1_date"] == dates[i + 1]
    assert row["v21_entry_date"] == dates[i + 1]
    assert row["v22_entry_date"] == dates[i + 2]
    assert row["v22_entry_close"] == 105.5
    assert len(h) == 8
    assert set(h.horizon) == set(HORIZONS)


def test_losing_t1_breakout_is_rejected_without_v22_position():
    positions, prices, dates, i = fixture()
    prices.loc[prices.date == dates[i + 1], "close"] = 102
    positions.loc[0, "entry_close"] = 102
    signals, h = evaluate_recorded_signals(positions, prices, dates)
    assert signals.iloc[0]["check_status"] == "未通過"
    assert pd.isna(signals.iloc[0]["v22_entry_close"])
    assert set(h.strategy) == {"V2.1次日代理進場"}


def test_do_not_chase_more_than_five_percent_next_close():
    status, why = check_next_close("收盤突破觀察", 100, 99, 106, 98)
    assert status == "未通過"
    assert "5%" in why


def test_pullback_requires_reference_and_current_ma20():
    assert check_next_close("回測初步止跌", 100, 98, 99, 98)[0] == "通過"
    assert check_next_close("回測初步止跌", 100, 98, 99, 100)[0] == "未通過"
    assert check_next_close("回測初步止跌", 100, 98, None, 100)[0] == "無法確認"


def test_no_t1_future_data_may_be_read():
    positions, prices, dates, i = fixture()
    out, h = evaluate_recorded_signals(
        positions, prices, dates, asof=dates[i]
    )
    assert out.iloc[0]["check_status"] == "等待T+1"
    assert pd.isna(out.iloc[0]["v22_entry_close"])
    assert h.empty


def test_confirmed_t1_but_no_t2_is_pending():
    positions, prices, dates, i = fixture()
    out, h = evaluate_recorded_signals(
        positions, prices, dates, asof=dates[i + 1]
    )
    assert out.iloc[0]["check_status"] == "等待T+2"
    assert pd.isna(out.iloc[0]["v22_entry_close"])
    assert set(h.strategy) == {"V2.1次日代理進場"}
    assert set(h.status) == {"尚未到期"}


def test_no_t1_ma20_due_to_quote_gap():
    positions, prices, dates, i = fixture()
    prices = prices[prices.date != dates[i - 5]].copy()
    out, h = evaluate_recorded_signals(positions, prices, dates)
    assert out.iloc[0]["check_status"] == "無法確認"
    assert not (h.strategy == "V2.2隔日確認進場").any()


def test_t2_missing_quote_not_fabricated():
    positions, prices, dates, i = fixture()
    prices = prices[prices.date != dates[i + 2]].copy()
    out, h = evaluate_recorded_signals(positions, prices, dates)
    assert out.iloc[0]["check_status"] == "T+2缺價"
    assert pd.isna(out.iloc[0]["v22_entry_close"])
    assert not (h.strategy == "V2.2隔日確認進場").any()


def test_horizons_are_entry_plus_exact_sessions_and_include_costs():
    positions, prices, dates, i = fixture()
    signals, h = evaluate_recorded_signals(positions, prices, dates)
    h3 = h[(h.horizon == 3) & (h.strategy == "V2.1次日代理進場")].iloc[0]
    assert h3["evaluation_date"] == dates[i + 4]
    assert h3["status"] == "已完成"
    assert h3["net_pct"] < h3["gross_pct"]
    assert h3["worst_close_drawdown_pct"] <= 0
    h20 = h[(h.horizon == 20) & (h.strategy == "V2.2隔日確認進場")].iloc[0]
    assert h20["evaluation_date"] == dates[i + 22]


def test_gap_in_horizon_path_not_reported_as_profit():
    positions, prices, dates, i = fixture()
    prices = prices[prices.date != dates[i + 4]].copy()
    _, h = evaluate_recorded_signals(positions, prices, dates)
    h3 = h[(h.horizon == 3) & (h.strategy == "V2.1次日代理進場")].iloc[0]
    assert h3["status"] == "行情不連續"
    assert pd.isna(h3["net_pct"])


def test_repaired_stock_close_conflict_is_not_silently_excluded():
    positions, prices, dates, i = fixture()
    positions.loc[0, "entry_close"] = 107  # conflicts with official T+1=105
    _, h = evaluate_recorded_signals(positions, prices, dates)
    h21 = h[h.strategy == "V2.1次日代理進場"]
    assert len(h21) == 4
    assert set(h21.status) == {"進場價與官方資料不一致"}
    assert h21.net_pct.isna().all()


def test_preprotocol_signal_only_diagnostic_not_v22_performance():
    positions, prices, dates, i = fixture()
    older = dates.index("2026-11-19")
    positions.loc[0, "signal_date"] = dates[older]
    positions.loc[0, "signal_close"] = float(prices.iloc[older]["close"])
    positions.loc[0, "invalidation_reference"] = 101.0
    positions.loc[0, "entry_date"] = dates[older + 1]
    positions.loc[0, "entry_close"] = float(prices.iloc[older + 1]["close"])
    signals, h = evaluate_recorded_signals(positions, prices, dates)
    assert signals.iloc[0]["cohort"] == "規則制定前示例"
    assert not (h.strategy == "V2.2隔日確認進場").any()
    assert summarize_matured(h).empty
    assert PROSPECTIVE_FROM == "2026-11-23"


def test_summary_counts_only_completed_prospective_samples():
    positions, prices, dates, i = fixture()
    _, h = evaluate_recorded_signals(positions, prices, dates)
    summary = summarize_matured(h)
    assert set(summary["交易日"]) == {3, 5, 10, 20}
    assert (summary["已到期樣本"] == 1).all()
    assert len(summary) == 8
    # Purposely don't call a 1-sample fraction a reliable accuracy score.
    assert not summary["獲利比例%"].isna().all()


def test_bad_input_duplicates_do_not_create_fake_outcomes():
    positions, prices, dates, i = fixture()
    with pytest.raises(ValueError, match="重複"):
        evaluate_recorded_signals(
            pd.concat([positions, positions], ignore_index=True), prices, dates
        )
    with pytest.raises(ValueError, match="重複"):
        evaluate_recorded_signals(
            positions, pd.concat([prices, prices], ignore_index=True), dates
        )


def test_empty_unripe_samples_do_not_show_success_rate():
    p, q, dates, i = fixture()
    assert summarize_matured(pd.DataFrame()).empty
    x, h = evaluate_recorded_signals(p.iloc[:0], q, dates)
    assert x.empty and h.empty


def test_streamlit_and_github_daily_report_are_read_only():
    import ast
    from pathlib import Path

    root = Path(__file__).parent
    app = (root / "app.py").read_text("utf-8")
    ast.parse(app)
    assert "🔬 V2.2 隔日確認" in app
    assert "evaluate_recorded_signals(" in app
    assert "前瞻樣本" in app
    assert "已到期樣本" in app
    cli = (root / "v22_research_report.py").read_text("utf-8")
    ast.parse(cli)
    assert "SELECT market,code,name,setup" in cli
    assert "INSERT INTO" not in cli
    assert "UPDATE stock_research" not in cli
    workflow = (root / ".github/workflows/v21_paper_tracking.yml").read_text("utf-8")
    assert "python v22_research_report.py" in workflow
