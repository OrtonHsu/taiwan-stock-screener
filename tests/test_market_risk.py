"""Isolated V2.3 research tests; no network, database or writes."""
import pandas as pd
import pytest
from market_risk import build_market_breadth, assess_risk


def sample(days=23, twse=900, tpex=700, falling=False):
    dates = pd.bdate_range("2026-09-01", periods=days)
    rows = []
    for d_idx, date in enumerate(dates):
        for market, count in (("TWSE", twse), ("TPEX", tpex)):
            for n in range(count):
                direction = (-1 if falling and n < count * .95 else 1)
                rows.append((date.date().isoformat(), market, f"{n:04d}",
                             100 + direction * d_idx, 1000))
    return pd.DataFrame(rows, columns=["date", "market", "code", "close", "volume"])


def test_empty_data_is_safe():
    data = pd.DataFrame(columns=["date", "market", "code", "close", "volume"])
    assert build_market_breadth(data).empty
    assert assess_risk({"data_status": "insufficient"})["level"] == "unknown"


def test_complete_healthy_market():
    result = build_market_breadth(sample())
    assert result.iloc[0]["data_status"] == "insufficient"
    assert result.iloc[-1]["data_status"] == "ok"
    assert result.iloc[-1]["advancers"] == 1600
    assert assess_risk(result.iloc[-1].to_dict())["level"] == "breadth_normal"
    assert result.iloc[-1]["volume_ratio"] == pytest.approx(1.0)


def test_severe_market_breadth_does_not_enable_entry_gating():
    result = build_market_breadth(sample(falling=True))
    risk = assess_risk(result.iloc[-1].to_dict())
    assert risk["level"] == "breadth_stress"
    assert risk["breadth_score"] == 30
    assert risk["can_gate_entries"] is False


def test_missing_market_cannot_be_green():
    result = build_market_breadth(sample(twse=899))
    assert (result.data_status == "insufficient").all()
    assert assess_risk(result.iloc[-1].to_dict())["level"] == "unknown"


def test_duplicate_records_rejected():
    data = sample(days=2)
    with pytest.raises(ValueError, match="Duplicate"):
        build_market_breadth(pd.concat([data, data.iloc[:1]], ignore_index=True))


def test_incomplete_day_breaks_comparison():
    data = sample(days=3)
    middle = sorted(data.date.unique())[1]
    data = data[~((data.date == middle) & (data.market == "TWSE"))]
    result = build_market_breadth(data)
    assert result.iloc[1]["data_status"] == "insufficient"
    assert result.iloc[2]["data_status"] == "insufficient"
