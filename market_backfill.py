
"""TWSE monthly history backfill with persistent progress tracking."""

import argparse
import os
import re
import time
from datetime import date
from zoneinfo import ZoneInfo

import psycopg
import requests

from screener import num
from storage import Store

URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
HEADERS = {
    "User-Agent": "Mozilla/5.0 TaiwanStockScreener/1.2",
    "Accept": "application/json",
}


def months_back(count):
    today = date.today()
    year, month = today.year, today.month

    for _ in range(count):
        yield date(year, month, 1)
        month -= 1
        if month == 0:
            year -= 1
            month = 12


def parse_month(payload, code, expected_month):
    if payload.get("stat") != "OK":
        raise ValueError(f"官方狀態異常: {payload.get('stat')}")

    fields = payload.get("fields", [])
    required = ["日期", "成交股數", "成交金額", "收盤價"]

    if not all(key in fields for key in required):
        raise ValueError(f"官方欄位異動: {fields}")

    positions = {key: fields.index(key) for key in required}
    source_rows = payload.get("data", [])

    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError("官方沒有提供歷史行情")

    records = []
    seen_dates = set()

    for item in source_rows:
        try:
            raw = str(item[positions["日期"]]).strip()
            match = re.fullmatch(
                r"(\d{2,3})/(\d{1,2})/(\d{1,2})", raw
            )

            if not match:
                raise ValueError("日期格式異常")

            year, month, day = map(int, match.groups())
            trading_day = date(year + 1911, month, day)

            if trading_day.strftime("%Y-%m") != expected_month:
                raise ValueError("回傳日期不屬於要求月份")

            if trading_day in seen_dates:
                raise ValueError("官方資料有重複日期")

            close = num(item[positions["收盤價"]])
            volume = num(item[positions["成交股數"]])
            amount = num(item[positions["成交金額"]])

            if any(v is None for v in (close, volume, amount)):
                raise ValueError("行情包含無效數值")

            if close <= 0 or volume <= 0 or amount < 0:
                raise ValueError("行情數值不符合條件")

            records.append((
                trading_day.isoformat(),
                "TWSE",
                code,
                code,
                close,
                volume,
                amount,
            ))
            seen_dates.add(trading_day)

        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError(
                f"月份資料未完整通過驗證: {exc}"
            ) from exc

    return records


def create_progress_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS backfill_progress (
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                month TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                completed_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (market, code, month)
            )
        """)
    conn.commit()


def is_completed(conn, code, month):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1
            FROM backfill_progress
            WHERE market = 'TWSE'
              AND code = %s
              AND month = %s
        """, (code, month))
        return cur.fetchone() is not None


def mark_completed(conn, code, month, row_count):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO backfill_progress
                (market, code, month, row_count)
            VALUES ('TWSE', %s, %s, %s)
            ON CONFLICT (market, code, month)
            DO UPDATE SET
                row_count = EXCLUDED.row_count,
                completed_at = CURRENT_TIMESTAMP
        """, (code, month, row_count))
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", required=True)
    parser.add_argument("--months", type=int, default=4)
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()

    codes = list(dict.fromkeys(
        code.strip()
        for code in args.codes.split(",")
        if code.strip()
    ))

    if not 1 <= len(codes) <= 20:
        raise ValueError("每批只允許 1 至 20 檔股票")

    if not all(re.fullmatch(r"\d{4}", c) for c in codes):
        raise ValueError("股票代號必須是四碼數字")

    if not 1 <= args.months <= 12:
        raise ValueError("回補月份必須介於 1 至 12")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("缺少 DATABASE_URL")

    written = 0
    skipped = 0
    failures = []

    today = date.today()
    current_month = today.strftime("%Y-%m")

    with psycopg.connect(database_url) as progress_conn:
        create_progress_table(progress_conn)

        db = Store()
        try:
            with requests.Session() as session:
                for code in codes:
                    for month in months_back(args.months):
                        month_key = month.strftime("%Y-%m")
                        label = f"{code} {month_key}"

                        if (
                            month_key != current_month
                            and is_completed(
                                progress_conn, code, month_key
                            )
                        ):
                            skipped += 1
                            print(f"SKIPPED {label}: 已完成")
                            continue

                        try:
                            response = session.get(
                                URL,
                                params={
                                    "date": month.strftime("%Y%m%d"),
                                    "stockNo": code,
                                    "response": "json",
                                },
                                headers=HEADERS,
                                timeout=30,
                            )
                            response.raise_for_status()

                            rows = parse_month(
                                response.json(),
                                code,
                                month_key,
                            )

                            written += db.store(rows)

                            if month_key != current_month:
                                mark_completed(
                                    progress_conn,
                                    code,
                                    month_key,
                                    len(rows),
                                )

                            print(
                                f"OK {label}: {len(rows)} rows"
                            )

                        except Exception as exc:
                            failures.append(f"{label}: {exc}")
                            print(f"FAILED {label}: {exc}")

                        time.sleep(max(1.5, args.delay))
        finally:
            db.close()

    print(
        f"written={written}; "
        f"skipped={skipped}; "
        f"failed_requests={len(failures)}"
    )

    if failures:
        raise RuntimeError(
            "部分月份回補失敗，請檢查執行紀錄"
        )


if __name__ == "__main__":
    main()
