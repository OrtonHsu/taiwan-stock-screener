"""V1.5.1 dual-mode Taiwan stock research screener.

A: emerging strength with closing-price confirmation.
B: established uptrend pulling back, with a distinct watch/confirmed state.
These are closing-price heuristics, not buy instructions or backtested odds.
"""

import pandas as pd


OUTPUT_COLUMNS = [
    "date", "market", "code", "name", "close", "ma5", "ma10",
    "ma20", "ma60", "extension_pct", "change5_pct", "volume_ratio",
    "avg_turnover", "support_reference", "risk_pct", "risk_level",
    "risk_note", "history_days", "mode", "score", "signal",
]


def clamp(value, low=0, high=100):
    return max(low, min(high, float(value)))


def rank_v15(data, min_history=65, min_amount=50_000_000):
    """Return compatible V1.5.1 A/B results, including watch-only signals.

    Historical daily OHLC is not in the current data model, so confirmation
    means close-to-close stabilization, not proof of an intraday reversal.
    """
    if data is None or data.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    required = {"date", "market", "code", "name", "close", "volume", "amount"}
    if not required.issubset(data.columns):
        raise ValueError("行情缺少必要欄位")

    prices = data.copy()
    for column in ("date", "market", "code"):
        prices[column] = prices[column].astype(str)
    prices = prices[prices["market"].isin(("TWSE", "TPEX"))]
    if prices.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    latest_date = prices["date"].max()
    results = []

    for (market, code), g in prices.groupby(["market", "code"]):
        g = g.sort_values("date").drop_duplicates("date", keep="last").copy()
        if len(g) < max(65, min_history) or g["date"].iloc[-1] != latest_date:
            continue

        p = pd.to_numeric(g["close"], errors="coerce").reset_index(drop=True)
        v = pd.to_numeric(g["volume"], errors="coerce").reset_index(drop=True)
        amt = pd.to_numeric(g["amount"], errors="coerce").reset_index(drop=True)
        ma5 = p.rolling(5).mean()
        ma10 = p.rolling(10).mean()
        ma20 = p.rolling(20).mean()
        ma60 = p.rolling(60).mean()
        volume20 = v.rolling(20).mean()
        high20 = p.shift(1).rolling(20).max()

        needed = [
            p.iloc[-1], p.iloc[-2], p.iloc[-3], p.iloc[-6],
            ma5.iloc[-1], ma5.iloc[-2], ma10.iloc[-1],
            ma20.iloc[-1], ma20.iloc[-6], ma60.iloc[-1],
            volume20.iloc[-1], high20.iloc[-1],
        ]
        if any(pd.isna(x) for x in needed) or min(needed) <= 0:
            continue

        avg_amount = amt.tail(20).mean()
        if pd.isna(avg_amount) or avg_amount < min_amount:
            continue

        close = float(p.iloc[-1])
        previous = float(p.iloc[-2])
        prev2 = float(p.iloc[-3])
        m5, m10, m20, m60 = [float(x) for x in (
            ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1], ma60.iloc[-1]
        )]
        volume_ratio = float(v.iloc[-1]) / float(volume20.iloc[-1])
        if not 0 < volume_ratio < 20:
            continue

        prior_high = float(high20.iloc[-1])
        ma20_slope = (m20 / float(ma20.iloc[-6]) - 1) * 100
        extension = (close / m20 - 1) * 100
        change5 = (close / float(p.iloc[-6]) - 1) * 100
        previous20_peak = float(p.iloc[-21:-1].max())
        prior_strength = (previous20_peak / m20 - 1) * 100

        # Support based on closing data only, not actual intraday low.
        recent_closing_low = float(p.tail(10).min())
        support = min(m20 * 0.97, recent_closing_low * 0.99)
        support = min(support, close * 0.999)
        risk_pct = (close - support) / close * 100

        if extension > 10 or change5 > 15 or risk_pct > 8:
            risk_level = "高"
            risk_note = "乖離、短期漲幅或參考停損距離偏高，僅列風險觀察"
        elif extension > 5 or risk_pct > 5:
            risk_level = "中"
            risk_note = "進場距離偏大，等待價格確認"
        else:
            risk_level = "低"
            risk_note = "未觸發主要追價警示；不代表低投資風險"

        # A: avoid 'early strength' signals on persistent sharp declines.
        early = (
            ma20_slope > 0
            and 0.97 * m20 <= close <= 1.06 * m20
            and close >= m60 * 0.98
            and close <= prior_high * 1.035
            and 0.85 <= volume_ratio <= 2.5
            and change5 >= -4.0
            and close >= previous
            and m5 >= float(ma5.iloc[-2])
        )

        # B: genuine prior strength, price near MA20, orderly pullback.
        # The previous 20-day high must predate the latest close.
        pullback = (
            m20 > m60
            and ma20_slope > 0
            and prior_strength >= 6
            and 0.98 * m20 <= close <= 1.04 * m20
            and volume_ratio <= 1.3
            and -8 <= change5 <= 4
        )

        if not early and not pullback:
            continue

        # A simple close-based stabilization check. A down session stays
        # watch-only even when a strong historical trend is present.
        confirmed_close = close >= previous and (
            previous >= prev2 or close > m5
        )
        common = {
            "date": latest_date,
            "market": market,
            "code": code,
            "name": str(g["name"].iloc[-1]),
            "close": round(close, 2),
            "ma5": round(m5, 2),
            "ma10": round(m10, 2),
            "ma20": round(m20, 2),
            "ma60": round(m60, 2),
            "extension_pct": round(extension, 2),
            "change5_pct": round(change5, 2),
            "volume_ratio": round(volume_ratio, 2),
            "avg_turnover": round(float(avg_amount)),
            "support_reference": round(support, 2),
            "risk_pct": round(risk_pct, 2),
            "risk_level": risk_level,
            "risk_note": risk_note,
            "history_days": len(g),
        }

        if early:
            score = (
                clamp(35 - abs(extension) * 5, 0, 35)
                + clamp(ma20_slope * 12, 0, 25)
                + clamp(volume_ratio * 12, 0, 25)
                + clamp(15 - max(0, change5 - 5), 0, 15)
            )
            results.append({
                **common,
                "mode": "A 起漲前期",
                "score": round(clamp(score), 1),
                "signal": (
                    "僅供觀察：追價風險偏高" if risk_level == "高"
                    else "收盤初步轉強，仍待後續確認"
                ),
            })

        if pullback:
            score = (
                clamp(40 - abs(extension) * 8, 0, 40)
                + clamp(ma20_slope * 12, 0, 25)
                + clamp(prior_strength * 1.5, 0, 20)
                + clamp((1.5 - volume_ratio) * 10, 0, 15)
            )
            # Penalize steep declines instead of inadvertently rewarding
            # names that merely happen to be near their MA20.
            score -= max(0, -change5 - 3) * 3
            if not confirmed_close:
                score -= 15
            if risk_level == "高":
                signal = "僅供觀察：參考停損距離過大"
            elif confirmed_close:
                signal = "收盤初步止跌，仍待確認"
            else:
                signal = "等待止跌：尚無收盤確認"
            results.append({
                **common,
                "mode": "B 強勢回檔",
                "score": round(clamp(score), 1),
                "signal": signal,
            })

    if not results:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    ranked = pd.DataFrame(results)
    ranked["_risk_order"] = ranked["risk_level"].map(
        {"低": 0, "中": 1, "高": 2}
    )
    ranked["_watch_order"] = ranked["signal"].str.contains(
        "等待止跌|僅供觀察"
    ).astype(int)
    ranked = ranked.sort_values(
        ["mode", "_risk_order", "_watch_order", "score"],
        ascending=[True, True, True, False],
    ).drop(columns=["_risk_order", "_watch_order"])
    return ranked.reset_index(drop=True)[OUTPUT_COLUMNS]
