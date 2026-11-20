"""Regression tests for the uncalibrated close-only trend-following plan."""

import pandas as pd
import pytest

from trend_following import PLAN_COLUMNS, plan_trend_entries


def example(**changes):
    row = {
        "date": "2026-11-19",
        "market": "TWSE",
        "code": "2330",
        "name": "測試",
        "close": 102.0,
        "stage": "收盤突破觀察",
        "ma20": 100.0,
        "ma60": 96.0,
        "prior20_high": 100.0,
        "volume_ratio": 2.0,
        "breakout_pct": 2.0,
        "extension_pct": 2.0,
        "change5_pct": 4.0,
        "support_reference": 97.0,
        "support_distance_pct": 4.9,
        "data_quality": "近26個完整市場日連續；長期資料仍待驗收",
    }
    row.update(changes)
    return pd.DataFrame([row])


def test_breakout_has_conditional_trend_plan_not_purchase_instruction():
    out = plan_trend_entries(example())
    assert len(out) == 1
    stock = out.iloc[0]
    assert list(out.columns) == PLAN_COLUMNS
    assert stock["decision"] == "趨勢進場研究候選"
    assert stock["invalidation_reference"] == 100.0
    assert "下一個完整交易日" in stock["next_session_check"]
    assert "回測" not in stock["reason"]
    assert "買入" not in stock["decision"]


def test_uptrend_is_required_even_after_breakout():
    stock = plan_trend_entries(example(ma20=95, ma60=96)).iloc[0]
    assert stock["decision"] == "等待確認"
    assert "多頭排列" in stock["reason"]


def test_small_breakout_waits_instead_of_chasing():
    stock = plan_trend_entries(example(breakout_pct=0.3)).iloc[0]
    assert stock["decision"] == "等待確認"


def test_large_extension_and_support_gap_exclude_candidate():
    for change in (
        {"support_distance_pct": 8},
        {"change5_pct": 11},
    ):
        stock = plan_trend_entries(example(**change)).iloc[0]
        assert stock["decision"] == "暫不追價"


def test_existing_hot_or_support_risk_is_not_mislabeled_candidate():
    for stage in ("追價風險偏高", "支撐距離偏大"):
        stock = plan_trend_entries(example(stage=stage)).iloc[0]
        assert stock["decision"] == "暫不追價"


def test_pullback_close_confirmation_requires_near_ma20_and_trend():
    stock = plan_trend_entries(example(
        stage="回測初步止跌", close=101, extension_pct=1,
        breakout_pct=-1, volume_ratio=1.0,
    )).iloc[0]
    assert stock["decision"] == "趨勢進場研究候選"
    assert stock["invalidation_reference"] == 100.0
    stock = plan_trend_entries(example(
        stage="回測初步止跌", close=103, extension_pct=3,
        breakout_pct=-1, volume_ratio=1.0,
    )).iloc[0]
    assert stock["decision"] == "等待確認"


def test_invalid_date_mixture_and_columns_rejected():
    mixed = pd.concat([example(), example(date="2026-11-20")], ignore_index=True)
    with pytest.raises(ValueError, match="單一"):
        plan_trend_entries(mixed)
    with pytest.raises(ValueError, match="缺少欄位"):
        plan_trend_entries(example().drop(columns="ma20"))


def test_bad_numeric_data_never_becomes_candidate():
    out = plan_trend_entries(example(close=None))
    assert out.empty and list(out.columns) == PLAN_COLUMNS


def test_default_no_candidates_is_valid_result():
    assert plan_trend_entries(pd.DataFrame()).empty
    assert plan_trend_entries(None).empty


def test_trend_plan_does_not_mutate_input():
    inp = example()
    before = inp.copy(deep=True)
    plan_trend_entries(inp)
    pd.testing.assert_frame_equal(inp, before)


def test_app_keeps_focus_simple_and_has_detailed_research():
    import ast
    from pathlib import Path

    code = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    ast.parse(code)
    assert "from trend_following import plan_trend_entries" in code
    assert "趨勢進場研究候選" in code
    assert "next_session_check" in code
    assert "今日觀察" in code
    assert "進階分析" in code
