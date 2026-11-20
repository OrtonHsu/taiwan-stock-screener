"""V2 entry-timing *research* labels using verified end-of-day close/volume.

No intraday OHLC or corporate-action adjustment is assumed. All references
are computed as of the supplied last complete two-market snapshot, and
rolling breakout/volume references exclude the signal day's observation.
No trades, estimated win rates or database writes occur here.
"""

from __future__ import annotations

import pandas as pd


ENTRY_COLUMNS = [
    "date", "market", "code", "name", "close", "stage", "next_check",
    "reference_label", "reference_price", "ma5", "ma20", "ma60",
    "prior20_high", "volume_ratio", "extension_pct", "change5_pct",
    "support_reference", "support_distance_pct", "history_days",
    "avg_turnover", "data_quality",
]

STAGE_ORDER = {
    "回測初步止跌": 0,
    "收盤突破觀察": 1,
    "等待止跌確認": 2,
    "等待突破確認": 3,
    "追價風險偏高": 4,
}


def evaluate_entry_timing(
    prices: pd.DataFrame,
    complete_dates,
    min_history: int = 65,
    min_amount: float = 50_000_000,
) -> pd.DataFrame:
    """Assess *conditions* for entry research, not actionable buy signals.

    complete_dates MUST be prevalidated all-market snapshot dates, for
    example days with >=900 TWSE and >=700 TPEx codes. If the most recent
    26 complete days are not present for a stock, it is excluded.

    Historical MA60 uses 60 observed closes, not a corporate-action adjusted
    series. The latest 26 observations must be on consecutive complete market
    dates, but older data may remain under audit; label this honestly.
    """
    if min_history < 65:
        raise ValueError("至少需要 65 個歷史收盤觀測日")
    if min_amount < 0:
        raise ValueError("成交金額門檻不可為負")
    if prices is None or prices.empty:
        return pd.DataFrame(columns=ENTRY_COLUMNS)

    required = {"date", "market", "code", "name", "close", "volume", "amount"}
    if not required.issubset(prices.columns):
        raise ValueError(f"缺少行情欄位：{sorted(required - set(prices.columns))}")

    verified_dates = sorted(set(str(d) for d in complete_dates))
    if len(verified_dates) < 26:
        return pd.DataFrame(columns=ENTRY_COLUMNS)

    asof = verified_dates[-1]
    recent_dates = verified_dates[-26:]

    quotes = prices.copy()
    for key in ("date", "market", "code"):
        quotes[key] = quotes[key].astype(str).str.strip()
    # Do not use future prices, even if the caller loaded newer partial days.
    quotes = quotes[
        quotes["date"].le(asof)
        & quotes["market"].isin(("TWSE", "TPEX"))
        & quotes["code"].str.fullmatch(r"[1-9]\d{3}")
    ].copy()
    for key in ("close", "volume", "amount"):
        quotes[key] = pd.to_numeric(quotes[key], errors="coerce")
    quotes = quotes.dropna(subset=["close", "volume", "amount"])
    quotes = quotes[
        (quotes["close"] > 0)
        & (quotes["volume"] > 0)
        & (quotes["amount"] >= 0)
    ]
    if quotes.empty:
        return pd.DataFrame(columns=ENTRY_COLUMNS)

    quotes = quotes.sort_values(["market", "code", "date"]).drop_duplicates(
        ["market", "code", "date"], keep="last"
    )
    result = []

    for (market, code), group in quotes.groupby(["market", "code"], sort=False):
        if len(group) < min_history or group.iloc[-1]["date"] != asof:
            continue
        # An intermittent missing day must not masquerade as a stable pullback.
        if group["date"].iloc[-26:].tolist() != recent_dates:
            continue

        g = group.reset_index(drop=True)
        close_s, volume_s, amount_s = g["close"], g["volume"], g["amount"]
        ma5_s = close_s.rolling(5).mean()
        ma20_s = close_s.rolling(20).mean()
        ma60_s = close_s.rolling(60).mean()

        close = float(close_s.iloc[-1])
        prev_close, prev2_close = map(float, close_s.iloc[[-2, -3]])
        ma5, ma20, ma60 = map(float, (
            ma5_s.iloc[-1], ma20_s.iloc[-1], ma60_s.iloc[-1]
        ))
        old_ma20 = float(ma20_s.iloc[-6])
        avg_vol_prior20 = float(volume_s.shift(1).rolling(20).mean().iloc[-1])
        avg_turnover = float(amount_s.shift(1).rolling(20).mean().iloc[-1])
        if not all(x > 0 for x in (
            close, ma5, ma20, ma60, old_ma20, avg_vol_prior20
        )):
            continue
        if pd.isna(avg_turnover) or avg_turnover < min_amount:
            continue

        volume_ratio = float(volume_s.iloc[-1]) / avg_vol_prior20
        if volume_ratio <= 0 or volume_ratio > 20:
            continue

        prior20_high = float(close_s.iloc[-21:-1].max())
        prior10_low = float(close_s.iloc[-11:-1].min())
        extension = (close / ma20 - 1) * 100
        change5 = (close / float(close_s.iloc[-6]) - 1) * 100
        # Historical closing-price reference, not an executable stop price.
        support = min(ma20 * 0.98, prior10_low * 0.99, close * 0.999)
        support_distance = (close / close - support / close) * 100

        # Focus on stable/upward regimes; don't portray downtrends as bargains.
        rising_trend = ma20 > old_ma20 and ma20 >= 0.99 * ma60
        if not rising_trend or close < 0.98 * ma60:
            continue

        if extension > 8 or change5 > 12 or support_distance > 8:
            stage = "追價風險偏高"
            ref_label, ref_price = "MA20（僅參考）", ma20
            next_check = "股價乖離、短漲幅或距收盤支撐過遠，先等整理／重新評估"
        elif (
            close > prior20_high
            and volume_ratio >= 1.5
            and close > ma20
            and ma5 >= ma20
        ):
            stage = "收盤突破觀察"
            ref_label, ref_price = "前20日最高收盤", prior20_high
            next_check = "隔日避免無條件追價；觀察回測突破參考價是否守住"
        elif (
            abs(extension) <= 3
            and close >= prev_close
            and prev_close <= prev2_close
            and close >= ma5
            and volume_ratio <= 1.8
            and ma20 >= ma60
        ):
            stage = "回測初步止跌"
            ref_label, ref_price = "MA20（收盤參考）", ma20
            next_check = "觀察後續收盤是否續守MA20；不代表盤中低點已確認"
        elif abs(extension) <= 3.5 and close < max(prev_close, ma5):
            stage = "等待止跌確認"
            ref_label, ref_price = "MA5（收盤參考）", ma5
            next_check = "尚未出現收盤轉強；觀察收盤重返MA5並高於前日"
        elif -1 <= extension <= 5 and close <= prior20_high:
            stage = "等待突破確認"
            ref_label, ref_price = "前20日最高收盤", prior20_high
            next_check = "等待收盤突破前20日高點，並比對前20日均量至少1.5倍"
        else:
            continue

        result.append({
            "date": asof,
            "market": market,
            "code": code,
            "name": str(g["name"].iloc[-1]),
            "close": round(close, 2),
            "stage": stage,
            "next_check": next_check,
            "reference_label": ref_label,
            "reference_price": round(ref_price, 2),
            "ma5": round(ma5, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "prior20_high": round(prior20_high, 2),
            "volume_ratio": round(volume_ratio, 2),
            "extension_pct": round(extension, 2),
            "change5_pct": round(change5, 2),
            "support_reference": round(support, 2),
            "support_distance_pct": round(support_distance, 2),
            "history_days": len(g),
            "avg_turnover": round(avg_turnover),
            "data_quality": "近26個完整市場日連續；長期資料仍待驗收",
        })

    if not result:
        return pd.DataFrame(columns=ENTRY_COLUMNS)

    out = pd.DataFrame(result)
    out["_priority"] = out["stage"].map(STAGE_ORDER)
    return (
        out.sort_values(
            ["_priority", "support_distance_pct", "avg_turnover", "market", "code"],
            ascending=[True, True, False, True, True]
        )
        .drop(columns="_priority")
        .reset_index(drop=True)[ENTRY_COLUMNS]
    )
