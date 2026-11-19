"""Focused READ-ONLY preview for the 7713 October 2026 TPEx quote gaps.

The 2026-11-19 official gap audit identified valid TPEx closes on three
dates absent from public.prices. Re-fetch the official monthly payload,
validate all three dates and prices, and re-check current database rows.
This script DOES NOT insert/update/delete or mark progress.
"""

import json
import os
from datetime import date

import psycopg
import requests

from tpex_backfill import get_month, parse_month

CODE = "7713"
MARKET = "TPEX"
MONTH = date(2026, 10, 1)
TARGET_DATES = ("2026-10-27", "2026-10-29", "2026-10-30")


def check_official_rows(rows):
    """Refuse to certify a repair if any expected official close is missing."""
    by_date = {}
    for row in rows:
        day, market, code, name, close, volume, amount = row
        day = str(day)
        if market != MARKET or code != CODE or day[:7] != "2026-10":
            raise ValueError(f"官方股票／市場／月份與目標不符：{row[:3]}")
        if day in by_date:
            raise ValueError(f"官方同日重複報價：{day}")
        if close is None or float(close) <= 0:
            raise ValueError(f"官方收盤價無效：{day}")
        if volume is None or float(volume) < 0 or amount is None or float(amount) < 0:
            raise ValueError(f"官方成交量／金額無效：{day}")
        by_date[day] = row

    missing_in_official = sorted(set(TARGET_DATES) - set(by_date))
    if missing_in_official:
        raise ValueError("官方缺少需要補存的有效收盤價：" + ",".join(missing_in_official))
    return {day: by_date[day] for day in TARGET_DATES}


def prepare_report(official_rows, database_rows):
    """Classify exact target dates; fail closed on unexpected market matches."""
    verified = check_official_rows(official_rows)
    existing = {}
    other_market = []
    for row in database_rows:
        day, market, code = str(row[0]), str(row[1]), str(row[2])
        if day not in TARGET_DATES or code != CODE:
            raise ValueError(f"資料庫回傳不符合指定日期／股票：{day} {code}")
        if market != MARKET:
            other_market.append({"date": day, "market": market})
            continue
        if day in existing:
            raise ValueError(f"資料庫出現同一市場／日期重複紀錄：{day}")
        existing[day] = row

    prices = []
    for day in TARGET_DATES:
        row = verified[day]
        prices.append({
            "date": day, "market": MARKET, "code": CODE,
            "name": str(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "amount": float(row[6]),
            "status": "already_present" if day in existing else "missing_in_database",
        })

    return {
        "mode": "READ_ONLY", "code": CODE, "market": MARKET, "month": "2026-10",
        "expected_targets": len(TARGET_DATES),
        "official_valid_targets": len(verified),
        "missing_dates": [day for day in TARGET_DATES if day not in existing],
        "already_stored_dates": [day for day in TARGET_DATES if day in existing],
        "other_market_records": other_market,
        "official_quotes": prices,
        # A candidate is NOT permission to write: require an explicit approval,
        # plus a separate controlled insert plan in a future change.
        "ready_for_manual_review": not other_market,
    }


def markdown_report(report):
    lines = [
        "## V2 7713 官方月行情補檔前確認（唯讀）",
        f"標的：**{report['market']} {report['code']}**；期間：**{report['month']}**",
        f"官方確認有有效收盤價：**{report['official_valid_targets']} 筆**。",
        f"目前資料庫仍未存：**{len(report['missing_dates'])} 筆**；"
        f"已存在：**{len(report['already_stored_dates'])} 筆**。",
        "",
        "| 日期 | 官方收盤價 | 成交股數 | 成交金額 | 資料庫狀態 |",
        "|---|---:|---:|---:|---|",
    ]
    for row in report["official_quotes"]:
        lines.append(
            f"| {row['date']} | {row['close']} | {row['volume']} | "
            f"{row['amount']} | {row['status']} |"
        )
    if report["other_market_records"]:
        lines.append("\n**另有不同市場標記紀錄，修復前需人工核對：**")
        for rec in report["other_market_records"]:
            lines.append(f"- {rec['date']}：{rec['market']}")
    lines.append(
        "\n**本次不寫入 Supabase、不補價、不修改任何回補進度標記。** "
        "官方值與本地缺漏經本次核對後，仍須明確核准安全補存流程。"
    )
    return "\n".join(lines) + "\n"


def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("Missing DATABASE_URL")

    # Exactly one stock/month request; the existing parser checks ROC dates,
    # quote validity, price units, and duplicate dates.
    with requests.Session() as session:
        payload = get_month(session, CODE, MONTH)
        official_rows = parse_month(payload, CODE, MONTH.strftime("%Y-%m"))

    with psycopg.connect(db_url, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                """SELECT date, market, code, name, close, volume, amount
                   FROM public.prices
                   WHERE code = %s AND date = ANY(%s)
                   ORDER BY date, market""",
                (CODE, list(TARGET_DATES)),
            )
            stored_rows = cur.fetchall()

    report = prepare_report(official_rows, stored_rows)
    print("TPEX_7713_SAFE_PREVIEW " + json.dumps(
        report, ensure_ascii=False, sort_keys=True
    ), flush=True)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(markdown_report(report))


if __name__ == "__main__":
    main()
