
"""TWSE/TPEx stock screening with conservative risk indicators."""

from __future__ import annotations

import io
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests


TWSE = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

DB = Path("data/quotes.sqlite3")

HEADERS = {
    "User-Agent": "TaiwanStockScreener/1.0 (personal research)",
    "Accept": "application/json",
}


def num(x):
    """Convert an official quote value to a number."""
    if x is None:
        return None

    s = str(x).replace(",", "").replace("＋", "+").strip()
    s = re.sub(r"<[^>]+>", "", s).strip()

    if s in (
        "",
        "-",
        "--",
        "---",
        "除權",
        "除息",
        "N/A",
        "nan",
    ):
        return None

    try:
        value = float(s)
        if not pd.notna(value):
            return None
        return value
    except (ValueError, TypeError):
        return None


def field(row, *keys):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def get_json(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=25,
    )
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, list):
        raise ValueError(
            f"官方 API 回傳非預期格式：{url}"
        )

    return payload


def parse_twse(rows, date):
    out = []

    for row in rows:
        code = str(
            field(row, "Code", "證券代號") or ""
        ).strip()

        if not re.fullmatch(r"\d{4}", code):
            continue

        price = num(
            field(row, "ClosingPrice", "收盤價")
        )
        volume = num(
            field(row, "TradeVolume", "成交股數")
        )
        amount = num(
            field(row, "TradeValue", "成交金額")
        )

        if (
            price is None
            or volume is None
            or amount is None
            or price <= 0
            or volume <= 0
            or amount < 0
        ):
            continue

        out.append(
            (
                date,
                "TWSE",
                code,
                str(
                    field(row, "Name", "證券名稱") or code
                ),
                price,
                volume,
                amount,
            )
        )

    return out


def parse_tpex(rows, date):
    out = []

    for row in rows:
        code = str(
            field(
                row,
                "SecuritiesCompanyCode",
                "SecuritiesCode",
                "代號",
                "證券代號",
            ) or ""
        ).strip()

        if not re.fullmatch(r"\d{4}", code):
            continue

        price = num(
            field(
                row,
                "Close",
                "ClosingPrice",
                "收盤",
                "收盤價",
            )
        )

        volume = num(
            field(
                row,
                "TradingShares",
                "TradeVolume",
                "成交股數",
                "成交量",
            )
        )

        amount = num(
            field(
                row,
                "TransactionAmount",
                "TradingAmount",
                "TradeValue",
                "成交金額",
            )
        )

        if (
            price is None
            or volume is None
            or amount is None
            or price <= 0
            or volume <= 0
            or amount < 0
        ):
            continue

        out.append(
            (
                date,
                "TPEX",
                code,
                str(
                    field(
                        row,
                        "CompanyName",
                        "Name",
                        "名稱",
                        "證券名稱",
                    ) or code
                ),
                price,
                volume,
                amount,
            )
        )

    return out


def connect(path=DB):
    """Legacy local SQLite helper."""

    path = Path(path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(path)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            date TEXT NOT NULL,
            market TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            amount REAL,
            PRIMARY KEY (date, market, code)
        )
        """
    )

    conn.commit()
    return conn


def store(conn, rows):
    """Write rows to the legacy local SQLite database."""

    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO prices
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

    return len(rows)


def fetch_snapshot(date=None):
    """Legacy snapshot preview. Does not verify official dates.

    Never use its output to write current-day market data.
    The verified production updater is daily_update.py.
    """

    raise RuntimeError(
        "舊版 fetch_snapshot 已停用："
        "請使用 daily_update.py 取得並驗證官方交易日期"
    )


def update(conn, date=None):
    """Block legacy unverified snapshot database writes."""

    raise RuntimeError(
        "舊版 update 已停用："
        "每日行情請由 daily_update.py 更新"
    )


def load(conn):
    return pd.read_sql_query(
        """
        SELECT *
        FROM prices
        ORDER BY date, market, code
        """,
        conn,
    )


def rank_stocks(
    data,
    min_history=65,
    min_amount=50_000_000,
    vol_ratio=1.5,
):
    """Rank technical breakout candidates and assess chase risk.

    Scores and risk levels are heuristics, not trading predictions.
    """

    if data.empty:
        return pd.DataFrame()

    candidates = []

    for (market, code), g in data.groupby(
        ["market", "code"]
    ):
        g = (
            g.sort_values("date")
            .drop_duplicates("date")
            .copy()
        )

        if len(g) < max(min_history, 65):
            continue

        p = pd.to_numeric(
            g["close"],
            errors="coerce",
        )

        v = pd.to_numeric(
            g["volume"],
            errors="coerce",
        )

        amt = pd.to_numeric(
            g["amount"],
            errors="coerce",
        )

        ma20 = p.rolling(20).mean()
        ma60 = p.rolling(60).mean()

        avgvol = v.rolling(20).mean()

        # Exclude today's close from breakout reference.
        high20 = p.shift(1).rolling(20).max()

        last = g.iloc[-1]

        needed = [
            p.iloc[-1],
            v.iloc[-1],
            ma20.iloc[-1],
            ma60.iloc[-1],
            ma60.iloc[-6],
            avgvol.iloc[-1],
            high20.iloc[-1],
        ]

        if any(pd.isna(x) for x in needed):
            continue

        close = float(p.iloc[-1])
        volume = float(v.iloc[-1])
        ma20_now = float(ma20.iloc[-1])
        ma60_now = float(ma60.iloc[-1])
        previous_ma60 = float(ma60.iloc[-6])
        prior_high = float(high20.iloc[-1])
        avg_volume = float(avgvol.iloc[-1])

        if min(
            close,
            volume,
            ma20_now,
            ma60_now,
            previous_ma60,
            prior_high,
            avg_volume,
        ) <= 0:
            continue

        # Preserve original 20-day average turnover rule.
        avg_amount = amt.tail(20).mean()

        if (
            pd.isna(avg_amount)
            or avg_amount < min_amount
        ):
            continue

        # Original uptrend rule.
        if not (
            close > ma20_now > ma60_now
        ):
            continue

        # Original 20-day close breakout.
        if close <= prior_high:
            continue

        # Original volume expansion rule.
        ratio = volume / avg_volume

        if ratio < vol_ratio:
            continue

        # Original 60-day MA rising rule.
        slope = ma60_now / previous_ma60 - 1

        if slope <= 0:
            continue

        # Original technical score, maximum 100.
        momentum = max(
            0,
            min(
                35,
                (close / ma60_now - 1) * 250,
            ),
        )

        volume_score = min(
            30,
            ratio * 10,
        )

        breakout_score = min(
            25,
            max(
                0,
                (close / prior_high - 1) * 1000,
            ),
        )

        slope_score = min(
            10,
            slope * 300,
        )

        technical_score = round(
            momentum
            + volume_score
            + breakout_score
            + slope_score,
            1,
        )

        # How far price has extended above MA20.
        extension_pct = (
            close / ma20_now - 1
        ) * 100

        # Technical support reference, not a stop order.
        stop_reference = max(
            ma20_now,
            prior_high * 0.97,
        )

        # Ensure support reference does not exceed close.
        stop_reference = min(
            stop_reference,
            close,
        )

        risk_pct = (
            (close - stop_reference)
            / close
            * 100
        )

        # Illustrative chase-risk reference only.
        watch_ceiling = close * 1.02

        # Preliminary heuristic risk labels.
        if (
            extension_pct > 12
            or risk_pct > 8
        ):
            risk_level = "高"
            risk_note = (
                "短線偏熱，避免直接追價"
            )

        elif (
            extension_pct > 7
            or risk_pct > 5
        ):
            risk_level = "中"
            risk_note = (
                "等待回測或突破確認"
            )

        else:
            risk_level = "低"
            risk_note = (
                "符合初步觀察條件，仍需確認行情"
            )

        candidates.append(
            {
                "date": last["date"],
                "market": market,
                "code": code,
                "name": last["name"],
                "close": round(close, 2),
                "score": technical_score,
                "volume_ratio": round(ratio, 2),
                "ma20": round(ma20_now, 2),
                "ma60": round(ma60_now, 2),
                "breakout_ref": round(
                    prior_high, 2
                ),
                "avg_turnover": round(
                    float(avg_amount)
                ),
                "history_days": len(g),
                "extension_pct": round(
                    extension_pct, 2
                ),
                "stop_reference": round(
                    stop_reference, 2
                ),
                "risk_pct": round(
                    risk_pct, 2
                ),
                "watch_ceiling": round(
                    watch_ceiling, 2
                ),
                "risk_level": risk_level,
                "risk_note": risk_note,
            }
        )

    if not candidates:
        return pd.DataFrame()

    return (
        pd.DataFrame(candidates)
        .sort_values(
            ["score", "risk_pct"],
            ascending=[False, True],
        )
        .reset_index(drop=True)
    )


def import_csv(conn, raw):
    """Import validated CSV into the legacy local SQLite DB."""

    frame = pd.read_csv(
        io.BytesIO(raw),
        dtype={
            "code": str,
            "date": str,
            "market": str,
        },
    )

    required = [
        "date",
        "market",
        "code",
        "name",
        "close",
        "volume",
        "amount",
    ]

    if not set(required).issubset(
        frame.columns
    ):
        raise ValueError(
            "CSV 欄位必須包含："
            + ", ".join(required)
        )

    if not frame["date"].astype(str).str.fullmatch(
        r"\d{4}-\d{2}-\d{2}"
    ).all():
        raise ValueError(
            "date 必須為 YYYY-MM-DD"
        )

    if not frame["market"].isin(
        ["TWSE", "TPEX"]
    ).all():
        raise ValueError(
            "market 僅接受 TWSE 或 TPEX"
        )

    if not frame["code"].astype(str).str.fullmatch(
        r"\d{4}"
    ).all():
        raise ValueError(
            "code 必須為四碼股票代號"
        )

    rows = []

    for row in frame[required].itertuples(
        index=False,
        name=None,
    ):
        d, m, c, n, p, v, a = row

        price = num(p)
        volume = num(v)
        amount = num(a)

        if (
            price is None
            or volume is None
            or amount is None
            or price <= 0
            or volume <= 0
            or amount < 0
        ):
            continue

        rows.append(
            (
                str(d),
                str(m),
                str(c),
                str(n),
                price,
                volume,
                amount,
            )
        )

    return store(conn, rows)
