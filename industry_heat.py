
"""V1.4: Official industry classification and heat index.

The 0-100 score is an experimental activity/strength index.
It is NOT net capital inflow or a probability of price gains.
"""

import re

import pandas as pd
import requests


SOURCES = {
    "TWSE": (
        "https://openapi.twse.com.tw/v1/"
        "opendata/t187ap03_L"
    ),
    "TPEX": (
        "https://www.tpex.org.tw/openapi/v1/"
        "mopsfin_t187ap03_O"
    ),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

# Official TWSE/TPEx industry codes.
INDUSTRIES = {
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "14": "建材營造",
    "15": "航運業",
    "16": "觀光餐旅",
    "17": "金融保險",
    "18": "貿易百貨",
    "19": "綜合",
    "20": "其他",
    "21": "化學工業",
    "22": "生技醫療",
    "23": "油電燃氣",
    "24": "半導體",
    "25": "電腦及週邊",
    "26": "光電業",
    "27": "通信網路",
    "28": "電子零組件",
    "29": "電子通路",
    "30": "資訊服務",
    "31": "其他電子",
    "32": "文化創意",
    "33": "農業科技",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
}


def first(row, *names):
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def get_industries():
    """Download official listed/OTC company classifications."""

    output = []

    with requests.Session() as session:
        session.headers.update(HEADERS)

        for market, url in SOURCES.items():
            response = session.get(
                url,
                timeout=45,
            )
            response.raise_for_status()

            payload = response.json()

            if not isinstance(payload, list):
                raise ValueError(
                    f"{market}: 公司資料格式異常"
                )

            market_rows = []

            for row in payload:
                if not isinstance(row, dict):
                    continue

                code = first(
                    row,
                    "公司代號",
                    "SecuritiesCompanyCode",
                )

                raw_industry = first(
                    row,
                    "產業別",
                    "SecuritiesIndustryCode",
                )

                if not re.fullmatch(r"\d{4}", code):
                    continue

                match = re.fullmatch(
                    r"\d{1,2}",
                    raw_industry,
                )

                if not match:
                    continue

                industry_code = (
                    raw_industry.zfill(2)
                )

                industry = INDUSTRIES.get(
                    industry_code
                )

                if not industry:
                    continue

                market_rows.append({
                    "market": market,
                    "code": code,
                    "industry": industry,
                    "industry_code": industry_code,
                })

            if len(market_rows) < 100:
                raise ValueError(
                    f"{market}: 有效產業分類不足，"
                    "請確認官方 API 欄位"
                )

            output.extend(market_rows)

    return (
        pd.DataFrame(output)
        .drop_duplicates(
            ["market", "code"]
        )
        .reset_index(drop=True)
    )


def clamp(value, low=0.0, high=1.0):
    return max(
        low,
        min(high, float(value)),
    )


def calculate_industry_heat(
    prices,
    profiles,
    coverage_threshold=0.70,
    minimum_stocks=3,
):
    """Calculate industry heat on the latest six trading dates.

    Data coverage is checked against official company profiles.
    No score is produced when the available sample is too small.
    """

    required_prices = {
        "date",
        "market",
        "code",
        "close",
        "amount",
    }

    if not required_prices.issubset(
        prices.columns
    ):
        raise ValueError(
            "行情資料缺少必要欄位"
        )

    if prices.empty or profiles.empty:
        return pd.DataFrame()

    p = prices.copy()

    for column in (
        "date",
        "market",
        "code",
    ):
        p[column] = p[column].astype(str)

    p["close"] = pd.to_numeric(
        p["close"],
        errors="coerce",
    )

    p["amount"] = pd.to_numeric(
        p["amount"],
        errors="coerce",
    )

    p = p[
        (p["close"] > 0)
        & (p["amount"] >= 0)
    ].copy()

    p = p.drop_duplicates(
        ["date", "market", "code"],
        keep="last",
    )

    trading_dates = sorted(
        p["date"].unique()
    )

    if len(trading_dates) < 6:
        return pd.DataFrame()

    recent_dates = trading_dates[-6:]
    latest = recent_dates[-1]

    p = p[
        p["date"].isin(recent_dates)
    ].copy()

    meta = profiles[
        ["market", "code", "industry"]
    ].drop_duplicates(
        ["market", "code"]
    )

    p = p.merge(
        meta,
        on=["market", "code"],
        how="inner",
    )

    if p.empty:
        return pd.DataFrame()

    # Same security must have all six dates.
    counts = (
        p.groupby(["market", "code"])["date"]
        .nunique()
        .reset_index(name="days")
    )

    eligible = counts[
        counts["days"] == 6
    ][["market", "code"]]

    complete = p.merge(
        eligible,
        on=["market", "code"],
        how="inner",
    )

    # Official universe: each issuer belongs to one industry.
    universe = (
        meta.groupby("industry")[["market", "code"]]
        .apply(
            lambda g: len(g.drop_duplicates())
        )
        .to_dict()
    )

    total_official = len(meta)

    # Market-wide benchmark must also have adequate coverage.
    complete_stock_count = len(eligible)

    market_coverage = (
        complete_stock_count / total_official
        if total_official
        else 0
    )

    if complete.empty:
        return pd.DataFrame()

    close_wide = complete.pivot(
        index=["market", "code", "industry"],
        columns="date",
        values="close",
    )

    amount_wide = complete.pivot(
        index=["market", "code", "industry"],
        columns="date",
        values="amount",
    )

    if not set(recent_dates).issubset(
        close_wide.columns
    ):
        return pd.DataFrame()

    previous = recent_dates[-2]

    returns = (
        close_wide[latest]
        / close_wide[previous]
        - 1
    )

    # Equal-weight market reference,
    # NOT the actual TAIEX index return.
    market_return = (
        float(returns.mean())
        if not returns.empty
        else 0.0
    )

    stock_frame = pd.DataFrame({
        "return": returns,
        "current_amount": amount_wide[latest],
        "previous_amount": (
            amount_wide[recent_dates[:-1]]
            .mean(axis=1)
        ),
    }).reset_index()

    stock_frame = stock_frame.dropna()

    # Total turnover of the same complete-data universe.
    today_market_amount = float(
        stock_frame["current_amount"].sum()
    )

    previous_market_amount = float(
        stock_frame["previous_amount"].sum()
    )

    results = []

    for industry, group in stock_frame.groupby(
        "industry"
    ):
        official_count = universe.get(
            industry,
            0,
        )

        available_count = len(group)

        coverage = (
            available_count / official_count
            if official_count
            else 0
        )

        result = {
            "industry": industry,
            "date": latest,
            "industry_heat": None,
            "status": "資料不足",
            "coverage_pct": round(
                coverage * 100,
                1,
            ),
            "stocks_used": available_count,
            "official_stocks": official_count,
        }

        if (
            coverage < coverage_threshold
            or available_count < minimum_stocks
            or market_coverage < coverage_threshold
        ):
            results.append(result)
            continue

        today_amount = float(
            group["current_amount"].sum()
        )

        previous_amount = float(
            group["previous_amount"].sum()
        )

        if (
            today_amount <= 0
            or previous_amount <= 0
            or today_market_amount <= 0
            or previous_market_amount <= 0
        ):
            results.append(result)
            continue

        # 1. Turnover acceleration: 35 points.
        amount_ratio = (
            today_amount / previous_amount
        )

        turnover_score = (
            35
            * clamp(
                (amount_ratio - 0.7) / 1.0
            )
        )

        # 2. Percentage of stocks rising: 30 points.
        rising_ratio = float(
            (group["return"] > 0).mean()
        )

        breadth_score = (
            30 * rising_ratio
        )

        # 3. Relative strength: 25 points.
        # Comparison is against equal-weight covered stocks.
        industry_return = float(
            group["return"].mean()
        )

        relative_return = (
            industry_return - market_return
        )

        relative_score = (
            25
            * clamp(
                (relative_return + 0.02)
                / 0.04
            )
        )

        # 4. Change in market turnover share: 10 points.
        today_share = (
            today_amount / today_market_amount
        )

        previous_share = (
            previous_amount / previous_market_amount
        )

        share_ratio = (
            today_share / previous_share
            if previous_share > 0
            else 0
        )

        share_score = (
            10
            * clamp(
                (share_ratio - 0.8) / 0.5
            )
        )

        heat = (
            turnover_score
            + breadth_score
            + relative_score
            + share_score
        )

        result.update({
            "industry_heat": round(
                clamp(heat, 0, 100),
                1,
            ),
            "status": "可計算",
            "amount_ratio": round(
                amount_ratio,
                2,
            ),
            "rising_pct": round(
                rising_ratio * 100,
                1,
            ),
            "relative_return_pct": round(
                relative_return * 100,
                2,
            ),
        })

        results.append(result)

    # Include official industries with no complete data.
    existing = {
        row["industry"]
        for row in results
    }

    for industry, official_count in universe.items():
        if industry not in existing:
            results.append({
                "industry": industry,
                "date": latest,
                "industry_heat": None,
                "status": "資料不足",
                "coverage_pct": 0.0,
                "stocks_used": 0,
                "official_stocks": official_count,
            })

    return (
        pd.DataFrame(results)
        .sort_values(
            ["industry_heat", "industry"],
            ascending=[False, True],
            na_position="last",
        )
        .reset_index(drop=True)
    )


def build_industry_heat(prices):
    """Return (industry profiles, industry heat table)."""

    profiles = get_industries()

    heat = calculate_industry_heat(
        prices,
        profiles,
    )

    return profiles, heat
