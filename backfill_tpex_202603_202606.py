
"""Fixed-month TPEx backfill for 2026-03 through 2026-06.

Read official TPEx monthly quotes and insert missing records only.
Preserve existing TPEX prices and legacy TPEx progress markers.
"""

import argparse
import os
import re
import time
from datetime import date

import psycopg
import requests

from tpex_stock_list import get_tpex_stock_codes
from tpex_backfill import (
    HEADERS,
    create_progress_table,
    get_month,
    parse_month,
    store_without_overwriting,
    mark_completed,
)

TARGET_MONTHS = [
    date(2026, month, 1)
    for month in (3, 4, 5, 6)
]

PROGRESS_MARKET = "TPEx"


def get_pending(conn, codes):
    months = [m.strftime("%Y-%m") for m in TARGET_MONTHS]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT code, month
            FROM backfill_progress
            WHERE market = %s
              AND month = ANY(%s)
            """,
            (PROGRESS_MARKET, months),
        )
        done = {
            (str(code), month)
            for code, month in cur.fetchall()
        }

    pending = []

    for code in codes:
        missing = [
            month
            for month in months
            if (code, month) not in done
        ]
        if missing:
            pending.append((code, missing))

    return pending


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.batch_size <= 20:
        parser.error("每批股票數必須介於 1 至 20")
    if not 1.5 <= args.delay <= 30:
        parser.error("查詢間隔必須介於 1.5 至 30 秒")

    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("缺少 DATABASE_URL")

    codes = list(dict.fromkeys(
        code for code in get_tpex_stock_codes()
        if re.fullmatch(r"[1-9]\d{3}", code)
    ))

    if not codes:
        raise RuntimeError("未取得有效上櫃股票代號")

    with psycopg.connect(url, connect_timeout=15) as conn:
        create_progress_table(conn)
        pending = get_pending(conn, codes)

        print(f"股票清單：{len(codes)} 檔", flush=True)
        print(
            f"固定月份尚未完成：{len(pending)} 檔",
            flush=True,
        )

        selected = pending[:args.batch_size]
        if not selected:
            print("2026/03～06 已無待查月份", flush=True)
            return

        print(
            "本批股票：" +
            ",".join(code for code, _ in selected),
            flush=True,
        )

        inserted = 0
        completed = 0
        failures = []

        with requests.Session() as session:
            session.headers.update(HEADERS)

            for code, missing in selected:
                for month in TARGET_MONTHS:
                    key = month.strftime("%Y-%m")
                    if key not in missing:
                        continue

                    try:
                        payload = get_month(session, code, month)
                        records = parse_month(payload, code, key)

                        count = 0
                        if args.write:
                            count = store_without_overwriting(
                                conn, records
                            )
                            mark_completed(
                                conn, code, key, len(records)
                            )
                            completed += 1
                            inserted += count

                        print(
                            f"{'WRITTEN' if args.write else 'CHECK'} "
                            f"{code} {key}: "
                            f"{len(records)} valid; "
                            f"{count} inserted",
                            flush=True,
                        )

                    except Exception as exc:
                        conn.rollback()
                        failures.append(f"{code} {key}: {exc}")
                        print(
                            f"FAILED {code} {key}: {exc}",
                            flush=True,
                        )

                    time.sleep(args.delay)

        print("===== 固定月份回補結果 =====", flush=True)
        print(f"完成月份數：{completed}")
        print(f"新增行情筆數：{inserted}")
        print(f"失敗月份數：{len(failures)}")

        for failure in failures:
            print("FAILED_DETAIL", failure, flush=True)

        if failures:
            raise RuntimeError("部分月份失敗，請查看日誌")


if __name__ == "__main__":
    main()
