
"""V1.5 dual-mode Taiwan stock screening.

Mode A: early strength
Mode B: uptrend pullback

Research signals only, not guaranteed buy signals.
Uses daily closing prices, not intraday OHLC.
"""

import pandas as pd


def clamp(value, low=0, high=100):
    return max(low, min(high, float(value)))


def rank_v15(
    data,
    min_history=65,
    min_amount=50_000_000,
):
    """Return early-stage and pullback candidates."""

    if data is None or data.empty:
        return pd.DataFrame()

    required = {
        "date", "market", "code", "name",
        "close", "volume", "amount",
    }

    if not required.issubset(data.columns):
        raise ValueError("行情缺少必要欄位")

    prices = data.copy()

    prices["date"] = prices["date"].astype(str)
    prices["market"] = prices["market"].astype(str)
    prices["code"] = prices["code"].astype(str)

    # V1.5 only evaluates recognized markets.
    prices = prices[
        prices["market"].isin(["TWSE", "TPEX"])
    ].copy()

    if prices.empty:
        return pd.DataFrame()

    latest_date = prices["date"].max()

    results = []

    for (market, code), g in prices.groupby(
        ["market", "code"]
    ):
        g = (
            g.sort_values("date")
            .drop_duplicates("date", keep="last")
            .copy()
        )

        if len(g) < max(65, min_history):
            continue

        # Do not rank securities with stale snapshots.
        if g["date"].iloc[-1] != latest_date:
            continue

        p = pd.to_numeric(
            g["close"], errors="coerce"
        ).reset_index(drop=True)

        v = pd.to_numeric(
            g["volume"], errors="coerce"
        ).reset_index(drop=True)

        amt = pd.to_numeric(
            g["amount"], errors="coerce"
        ).reset_index(drop=True)

        ma5 = p.rolling(5).mean()
        ma10 = p.rolling(10).mean()
        ma20 = p.rolling(20).mean()
        ma60 = p.rolling(60).mean()

        avgvol20 = v.rolling(20).mean()
        high20 = p.shift(1).rolling(20).max()

        values = [
            p.iloc[-1], p.iloc[-2],
            p.iloc[-6],
            ma5.iloc[-1], ma10.iloc[-1],
            ma20.iloc[-1], ma20.iloc[-6],
            ma60.iloc[-1],
            avgvol20.iloc[-1],
            high20.iloc[-1],
        ]

        if any(pd.isna(x) for x in values):
            continue

        if min(values) <= 0:
            continue

        close = float(p.iloc[-1])
        previous = float(p.iloc[-2])

        m5 = float(ma5.iloc[-1])
        m10 = float(ma10.iloc[-1])
        m20 = float(ma20.iloc[-1])
        m60 = float(ma60.iloc[-1])

        volume_ratio = (
            float(v.iloc[-1])
            / float(avgvol20.iloc[-1])
        )

        avg_amount = float(amt.tail(20).mean())

        if (
            pd.isna(avg_amount)
            or avg_amount < min_amount
        ):
            continue

        if not 0 < volume_ratio < 20:
            continue

        prior_high = float(high20.iloc[-1])

        ma20_slope = (
            m20 / float(ma20.iloc[-6]) - 1
        ) * 100

        extension = (close / m20 - 1) * 100

        change5 = (
            close / float(p.iloc[-6]) - 1
        ) * 100

        recent_low = float(p.tail(10).min())

        # Closing-price support reference only.
        support = min(
            m20 * 0.97,
            recent_low * 0.99,
        )

        support = min(support, close * 0.999)

        risk_pct = (
            (close - support) / close * 100
        )

        if (
            extension > 10
            or change5 > 15
            or risk_pct > 8
        ):
            risk_level = "高"
            risk_note = "追高或停損距離過大，暫列觀察"
        elif (
            extension > 5
            or risk_pct > 5
        ):
            risk_level = "中"
            risk_note = "等待價格與支撐確認"
        else:
            risk_level = "低"
            risk_note = "未觸發主要追價警示"

        # Mode A: emerging trend, limited extension.
        early = (
            ma20_slope > 0
            and close >= m20 * 0.97
            and close <= m20 * 1.06
            and close >= m60 * 0.98
            and close <= prior_high * 1.035
            and close >= previous * 0.985
            and 0.85 <= volume_ratio <= 2.5
        )

        # Mode B: established trend pulling back
        # toward MA20, without a sharp close breakdown.
        prior_strength = (
            float(p.iloc[-21:-1].max())
            / m20 - 1
        ) * 100

        pullback = (
            m20 > m60
            and ma20_slope > 0
            and prior_strength >= 6
            and m20 * 0.98 <= close <= m20 * 1.04
            and close >= previous * 0.98
            and volume_ratio <= 1.3
        )

        if not early and not pullback:
            continue

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
            "avg_turnover": round(avg_amount),
            "support_reference": round(support, 2),
            "risk_pct": round(risk_pct, 2),
            "risk_level": risk_level,
            "risk_note": risk_note,
            "history_days": len(g),
        }

        if early:
            proximity = clamp(
                35 - abs(extension) * 5,
                0, 35,
            )

            trend = clamp(
                ma20_slope * 12,
                0, 25,
            )

            volume_score = clamp(
                volume_ratio * 12,
                0, 25,
            )

            strength = clamp(
                15 - max(0, change5 - 5),
                0, 15,
            )

            score = (
                proximity + trend
                + volume_score + strength
            )

            results.append({
                **common,
                "mode": "A 起漲前期",
                "score": round(clamp(score), 1),
                "signal": "初步轉強，等待確認",
            })

        if pullback:
            proximity = clamp(
                40 - abs(extension) * 8,
                0, 40,
            )

            trend = clamp(
                ma20_slope * 12,
                0, 25,
            )

            prior_score = clamp(
                prior_strength * 1.5,
                0, 20,
            )

            calm_volume = clamp(
                (1.5 - volume_ratio) * 10,
                0, 15,
            )

            score = (
                proximity + trend
                + prior_score + calm_volume
            )

            results.append({
                **common,
                "mode": "B 強勢回檔",
                "score": round(clamp(score), 1),
                "signal": "接近均線，等待止跌確認",
            })

    if not results:
        return pd.DataFrame()

    ranked = pd.DataFrame(results)

    # High-risk names remain visible for research,
    # but rank below lower-risk candidates.
    risk_order = {
        "低": 0,
        "中": 1,
        "高": 2,
    }

    ranked["_risk_order"] = (
        ranked["risk_level"].map(risk_order)
    )

    ranked = (
        ranked.sort_values(
            ["mode", "_risk_order", "score"],
            ascending=[True, True, False],
        )
        .drop(columns="_risk_order")
        .reset_index(drop=True)
    )

    return ranked
