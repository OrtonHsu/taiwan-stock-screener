
"""Automatic TPEx history backfill and recent-gap repair."""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg

from tpex_stock_list import get_tpex_stock_codes


def taiwan_today():
    return datetime.now(
        ZoneInfo("Asia/Taipei")
    ).date()


def get_target_months(count):
    today = taiwan_today()
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

        return {
            row[0]
            for row in cur.fetchall()
        }


def get_recent_gaps(conn, codes):
    """Find OTC codes missing recent TWSE trading dates.

    Only inspect dates belonging to the current
    Taiwan calendar month because the existing
    monthly backfill skips completed older months.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT date
            FROM public.prices
            WHERE market = 'TWSE'
              AND close > 0
            ORDER BY date DESC
            LIMIT 6
            """
        )

        recent_dates = [
            row[0]
            for row in cur.fetchall()
        ]

    if len(recent_dates) < 6:
        print(
            "最近交易日期不足6天，"
            "暫不啟動缺漏修復",
            flush=True,
        )
        return [], recent_dates

    current_month = taiwan_today().strftime(
        "%Y-%m"
    )

    repair_dates = [
        day
        for day in recent_dates
        if str(day)[:7] == current_month
    ]

    if not repair_dates:
        print(
            "最近6日均非當月，"
            "需要跨月專用修復流程",
            flush=True,
        )
        return [], recent_dates

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                code,
                COUNT(DISTINCT date)
            FROM public.prices
            WHERE market = 'TPEX'
              AND code = ANY(%s)
              AND date = ANY(%s)
              AND close > 0
              AND amount >= 0
            GROUP BY code
            """,
            (codes, repair_dates),
        )

        coverage = {
            str(code): days
            for code, days in cur.fetchall()
        }

    incomplete = [
        code
        for code in codes
        if coverage.get(code, 0)
        < len(repair_dates)
    ]

    print(
        "最近6個交易日："
        + ",".join(map(str, recent_dates)),
        flush=True,
    )

    print(
        "本次可修復日期："
        + ",".join(map(str, repair_dates)),
        flush=True,
    )

    print(
        f"當月行情缺漏候選："
        f"{len(incomplete)} 檔",
        flush=True,
    )

    return incomplete, recent_dates


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
    )

    args = parser.parse_args()

    if not 1 <= args.batch_size <= 20:
        raise ValueError(
            "每批股票必須介於1至20檔"
        )

    if not 2 <= args.months <= 12:
        raise ValueError(
            "月份必須介於2至12"
        )

    database_url = os.environ.get(
        "DATABASE_URL"
    )

    if not database_url:
        raise RuntimeError(
            "缺少 DATABASE_URL"
        )

    codes = [
        code
        for code in get_tpex_stock_codes()
        if re.fullmatch(r"[1-9]\d{3}", code)
    ]

    print(
        f"取得 {len(codes)} "
        "個上櫃證券候選代號",
        flush=True,
    )

    with psycopg.connect(database_url) as conn:
        completed = get_completed_codes(
            conn,
            args.months,
        )

        pending = [
            code
            for code in codes
            if code not in completed
        ]

        print(
            f"歷史月份待回補：{len(pending)} 檔",
            flush=True,
        )

        if pending:
            mode = "HISTORY"
            selected = pending[:args.batch_size]

        else:
            incomplete, recent_dates = (
                get_recent_gaps(conn, codes)
            )

            mode = "REPAIR_RECENT"
            selected = incomplete[
                :args.batch_size
            ]

    print(
        f"執行模式：{mode}",
        flush=True,
    )

    print(
        "本次選擇："
        + ",".join(selected),
        flush=True,
    )

    if not selected:
        print(
            "目前沒有可自動處理的股票",
            flush=True,
        )
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

    subprocess.run(
        command,
        check=True,
    )


if __name__ == "__main__":
    main()
