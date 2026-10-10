
"""Automatically select the next TWSE history backfill batch."""

import argparse
import os
import re
import subprocess
import sys
from datetime import date

import psycopg

from stock_list import get_twse_stock_codes


def get_completed_codes(conn, months):
    """Find codes with completed progress for all past months."""
    today = date.today()
    year, month = today.year, today.month
    target_months = []

    for _ in range(months):
        key = f"{year:04d}-{month:02d}"

        # Current month is always refreshed.
        if key != today.strftime("%Y-%m"):
            target_months.append(key)

        month -= 1
        if month == 0:
            year -= 1
            month = 12

    if not target_months:
        return set()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT code
            FROM backfill_progress
            WHERE market = 'TWSE'
              AND month = ANY(%s)
            GROUP BY code
            HAVING COUNT(DISTINCT month) = %s
            """,
            (target_months, len(target_months)),
        )
        return {row[0] for row in cur.fetchall()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--months", type=int, default=4)
    args = parser.parse_args()

    if not 1 <= args.batch_size <= 20:
        raise ValueError("每批股票數量必須介於 1 至 20")

    if not 1 <= args.months <= 12:
        raise ValueError("回補月份必須介於 1 至 12")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("缺少 DATABASE_URL")

    codes = get_twse_stock_codes()
    print(f"取得 {len(codes)} 個四碼上市證券代號")

    with psycopg.connect(database_url) as conn:
        completed = get_completed_codes(conn, args.months)

    pending = [
        code for code in codes
        if re.fullmatch(r"\d{4}", code)
        and code not in completed
    ]

    selected = pending[:args.batch_size]

    print(f"已完成：{len(completed)} 檔")
    print(f"待回補：{len(pending)} 檔")
    print(f"本次選擇：{','.join(selected)}")

    if not selected:
        print("所有符合條件的證券均已完成回補")
        return

    command = [
        sys.executable,
        "market_backfill.py",
        "--codes",
        ",".join(selected),
        "--months",
        str(args.months),
    ]

    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
