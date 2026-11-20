"""Research-only trend-following decision plan for end-of-day stock signals.

This is a *hypothesis* to test, not calibrated trading advice. Never report a
win rate or expected profit from these thresholds. Prices are signal-day
closes, not attainable next-session fills. No external I/O or market writes.
"""

from __future__ import annotations

import pandas as pd


PLAN_COLUMNS = [
    "date", "market", "code", "name", "close", "stage",
    "decision", "reason", "next_session_check", "invalidation_reference",
    "ma20", "ma60", "volume_ratio", "breakout_pct",
    "extension_pct", "support_distance_pct", "data_quality",
]

_ORDER = {"趨勢進場研究候選": 0, "等待確認": 1, "暫不追價": 2}


def plan_trend_entries(timing: pd.DataFrame) -> pd.DataFrame:
    """Select conditional watch candidates, based only on as-of EOD indicators.

    A candidate needs an upward MA regime plus conservative price, volume
    and risk-distance guards. Profitability is unknown pending properly
    calibrated chronological walk-forward backtesting with trading costs.
    These are close-based hypotheses; no intraday triggers or stops.
    """
    if timing is None or timing.empty:
        return pd.DataFrame(columns=PLAN_COLUMNS)

    required = {
        "date", "market", "code", "name", "close", "stage",
        "ma20", "ma60", "prior20_high", "volume_ratio",
        "breakout_pct", "extension_pct", "change5_pct",
        "support_reference", "support_distance_pct", "data_quality",
    }
    if not required.issubset(timing.columns):
        raise ValueError(f"進場研究資料缺少欄位：{sorted(required - set(timing.columns))}")

    candidates = timing.copy()
    for k in ("close", "ma20", "ma60", "prior20_high", "volume_ratio",
              "breakout_pct", "extension_pct", "change5_pct",
              "support_reference", "support_distance_pct"):
        candidates[k] = pd.to_numeric(candidates[k], errors="coerce")
    candidates = candidates.dropna(subset=[
        "close", "ma20", "ma60", "prior20_high", "volume_ratio",
        "breakout_pct", "extension_pct", "change5_pct",
        "support_reference", "support_distance_pct",
    ])
    candidates = candidates[
        (candidates["close"] > 0)
        & (candidates["ma20"] > 0)
        & (candidates["ma60"] > 0)
        & (candidates["prior20_high"] > 0)
        & (candidates["support_reference"] > 0)
    ]

    rows = []
    for stock in candidates.itertuples(index=False):
        stage = str(stock.stage)
        trend = stock.close > stock.ma20 > stock.ma60
        # A different reference is relevant to each setup. It is NOT a
        # stop order or a forecast of executable liquidation price.
        if stage == "收盤突破觀察":
            invalid = float(stock.prior20_high)
            check = (
                f"下一個完整交易日重新確認收盤能否維持在"
                f"{invalid:.2f}元之上；若跳空大漲不要無條件追價"
            )
        elif stage == "回測初步止跌":
            invalid = float(stock.ma20)
            check = (
                f"下一個完整交易日重新確認收盤是否守住"
                f"MA20約{invalid:.2f}元；若重新跌破，繼續等待"
            )
        else:
            invalid = float(stock.ma20)
            check = "等待有效趨勢、價格與量能確認，不因看起來便宜而猜底"

        if stage in ("追價風險偏高", "支撐距離偏大"):
            decision = "暫不追價"
            reason = (
                "價格過熱或距收盤支撐參考太遠；屬風險警示"
            )
        elif not trend:
            decision = "等待確認"
            reason = "尚未同時符合收盤價 > MA20 > MA60 的多頭排列"
        elif stock.support_distance_pct > 6 or stock.change5_pct > 8:
            decision = "暫不追價"
            reason = "距參考支撐超過6%或近5日收盤漲幅超過8%"
        elif (
            stage == "收盤突破觀察"
            and 0.5 <= stock.breakout_pct <= 3
            and 1.5 <= stock.volume_ratio <= 4
            and 0 <= stock.extension_pct <= 5
        ):
            decision = "趨勢進場研究候選"
            reason = (
                "多頭排列、前20日收盤高點突破0.5%～3%、"
                "量比1.5～4倍，且乖離與支撐距離在研究門檻內"
            )
        elif (
            stage == "回測初步止跌"
            and 0 <= stock.extension_pct <= 2
            and stock.volume_ratio <= 1.8
        ):
            decision = "趨勢進場研究候選"
            reason = "多頭排列、靠近MA20且收盤初步回穩；需次日再確認"
        else:
            decision = "等待確認"
            reason = "已觀察到型態，但尚未同時符合保守價量與風險門檻"

        rows.append({
            "date": str(stock.date), "market": str(stock.market),
            "code": str(stock.code), "name": str(stock.name),
            "close": round(float(stock.close), 2), "stage": stage,
            "decision": decision, "reason": reason,
            "next_session_check": check,
            "invalidation_reference": round(invalid, 2),
            "ma20": round(float(stock.ma20), 2),
            "ma60": round(float(stock.ma60), 2),
            "volume_ratio": round(float(stock.volume_ratio), 2),
            "breakout_pct": round(float(stock.breakout_pct), 2),
            "extension_pct": round(float(stock.extension_pct), 2),
            "support_distance_pct": round(float(stock.support_distance_pct), 2),
            "data_quality": str(stock.data_quality),
        })

    if not rows:
        return pd.DataFrame(columns=PLAN_COLUMNS)

    result = pd.DataFrame(rows)
    result["_order"] = result["decision"].map(_ORDER)
    return (
        result.sort_values(
            ["_order", "support_distance_pct", "code"],
            ascending=[True, True, True],
        )
        .drop(columns="_order")
        .reset_index(drop=True)[PLAN_COLUMNS]
    )
