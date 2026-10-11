
"""Automatically select the next TPEx history backfill batch."""

import argparse
import os
import re
import subprocess
import sys
from datetime import date

import psycopg

from tpex_stock_list import get_tpex_stock_codes


def get_target_months(count):
    today = date.today()
    year, month = today.year, today.month
    result = []

    for _ in range(count):
        key = f"{year:04d}-{month:02d}"

        if key != today.strftime("%Y-%m"):
            result.append(key)

        month -= 1
        if month == 0:
            year -= 1
            month = 12

    return result


def get_completed_codes(conn, months):
    target_months = get_target_months(months)

    if not target_months:
        return set()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT code
            FROM backfill_progress
            WHERE market = 'TPEx'
              AND month = ANY(%s)
            GROUP BY code
            HAVING COUNT(DISTINCT month) = %s
            """,
            (target_months, len(target_months)),
        )

        return {row[0] for row in cur.fetchall()}


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--months",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help="正式寫入 Supabase",
    )

    args = parser.parse_args()

    if not 1 <= args.batch_size <= 20:
        raise ValueError("每批股票數量必須介於 1 至 20")

    if not 2 <= args.months <= 12:
        raise ValueError("月份必須介於 2 至 12")

    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("缺少 DATABASE_URL")

    codes = get_tpex_stock_codes()

    print(f"取得 {len(codes)} 個上櫃證券候選代號")

    with psycopg.connect(database_url) as conn:
        completed = get_completed_codes(
            conn,
            args.months,
        )

    pending = [
        code
        for code in codes
        if re.fullmatch(r"[1-9]\d{3}", code)
        and code not in completed
    ]

    selected = pending[:args.batch_size]

    print(f"已完成：{len(completed)} 檔")
    print(f"待回補：{len(pending)} 檔")
    print(f"本次選擇：{','.join(selected)}")

    if not selected:
        print("全部候選證券已完成歷史回補")
        return

    command = [
        sys.executable,
        "tpex_backfill.py",
        "--codes",
        ",".join(selected),
        "--months",
        str(args.months),
    ]

    if args.write:
        command.append("--write")

    print(
        "執行模式：",
        "WRITE" if args.write else "DRY RUN",
    )

    subprocess.run(
        command,
        check=True,
    )


if __name__ == "__main__":
    main()
