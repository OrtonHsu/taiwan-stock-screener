"""V1.5.2: MA5/MA10/MA20 convergence scanner for Taiwan EOD quotes.

Uses closing prices only; no intraday breakout or execution claims.
This module does not write to the database.
"""

from __future__ import annotations

import pandas as pd


COLUMNS = [
    "date", "market", "code", "name", "close", "ma5", "ma10",
    "ma20", "convergence_pct", "extension_pct", "ma20_slope_pct",
    "volume_ratio", "avg_turnover", "status", "tightness",
    "history_days",
]


def scan_ma_convergence(
    data: pd.DataFrame,
    threshold_pct: float = 2.0,
    max_extension_pct: float = 3.0,
    min_amount: float = 50_000_000,
    min_history: int = 65,
) -> pd.DataFrame:
    """Identify MA5/MA10/MA20 convergence as of the latest market date.

    The convergence ratio is (max(MAs)-min(MAs)) / mean(MAs) * 100.
    """
    if not 0 < threshold_pct <= 10:
        raise ValueError("糾結率門檻須大於 0 且不超過 10%")
    if not 0 < max_extension_pct <= 20:
        raise ValueError("股價乖離限制須大於 0 且不超過 20%")
    if min_amount < 0:
        raise ValueError("成交金額下限不可為負")

    required = {"date", "market", "code", "name", "close", "volume", "amount"}
    if data is None or data.empty:
        return pd.DataFrame(columns=COLUMNS)
    if not required.issubset(data.columns):
        raise ValueError(f"缺少行情欄位：{sorted(required - set(data.columns))}")

    p = data.copy()
    for key in ("date", "market", "code"):
        p[key] = p[key].astype(str).str.strip()
    p = p[
        p["market"].isin(["TWSE", "TPEX"])
        & p["code"].str.fullmatch(r"[1-9]\d{3}")
    ].copy()
    if p.empty:
        return pd.DataFrame(columns=COLUMNS)

    parsed_dates = pd.to_datetime(p["date"], errors="coerce")
    p = p[parsed_dates.notna()].copy()
    if p.empty:
        return pd.DataFrame(columns=COLUMNS)
    for column in ("close", "volume", "amount"):
        p[column] = pd.to_numeric(p[column], errors="coerce")
    p = p.dropna(subset=["close", "volume", "amount"])
    p = p[(p.close > 0) & (p.volume >= 0) & (p.amount >= 0)].copy()
    if p.empty:
        return pd.DataFrame(columns=COLUMNS)

    latest_date = p["date"].max()
    output = []

    for (market, code), group in p.groupby(["market", "code"], sort=False):
        group = group.sort_values("date").drop_duplicates("date", keep="last")
        if len(group) < max(min_history, 25) or group.iloc[-1]["date"] != latest_date:
            continue

        close_series = group["close"].reset_index(drop=True)
        volume_series = group["volume"].reset_index(drop=True)
        amount_series = group["amount"].reset_index(drop=True)
        ma5_series = close_series.rolling(5, min_periods=5).mean()
        ma10_series = close_series.rolling(10, min_periods=10).mean()
        ma20_series = close_series.rolling(20, min_periods=20).mean()

        mas = [float(ma5_series.iloc[-1]), float(ma10_series.iloc[-1]), float(ma20_series.iloc[-1])]
        mean_ma = sum(mas) / 3
        if mean_ma <= 0:
            continue
        convergence_pct = (max(mas) - min(mas)) / mean_ma * 100
        last_close = float(close_series.iloc[-1])
        extension_pct = (last_close / mas[2] - 1) * 100
        if convergence_pct > threshold_pct or abs(extension_pct) > max_extension_pct:
            continue

        avg_turnover = float(amount_series.iloc[-20:].mean())
        avg_volume = float(volume_series.iloc[-20:].mean())
        if avg_turnover < min_amount or avg_volume <= 0:
            continue

        slope = (mas[2] / float(ma20_series.iloc[-6]) - 1) * 100
        volume_ratio = float(volume_series.iloc[-1]) / avg_volume
        prior_high = float(close_series.iloc[-21:-1].max())
        prior_close = float(close_series.iloc[-2])
        if slope >= 0 and mas[0] >= mas[1] >= mas[2] and last_close >= max(mas):
            status = "多頭轉強"
        elif slope < -0.5 and (last_close < mas[2] or mas[0] < mas[2]):
            status = "偏弱整理"
        else:
            status = "方向待確認"

        # Purely closing-price-derived breakout indication, not intraday evidence.
        if last_close > prior_high and volume_ratio >= 1.5:
            status += "／收盤突破觀察"

        output.append({
            "date": latest_date,
            "market": market,
            "code": code,
            "name": str(group.iloc[-1]["name"]),
            "close": round(last_close, 2),
            "ma5": round(mas[0], 2),
            "ma10": round(mas[1], 2),
            "ma20": round(mas[2], 2),
            "convergence_pct": round(convergence_pct, 3),
            "extension_pct": round(extension_pct, 2),
            "ma20_slope_pct": round(slope, 2),
            "volume_ratio": round(volume_ratio, 2),
            "avg_turnover": round(avg_turnover),
            "status": status,
            "tightness": "嚴格≤1%" if convergence_pct <= 1 else "標準≤2%",
            "history_days": len(group),
        })

    if not output:
        return pd.DataFrame(columns=COLUMNS)
    return pd.DataFrame(output).sort_values(
        ["convergence_pct", "ma20_slope_pct"], ascending=[True, False]
    ).reset_index(drop=True)
