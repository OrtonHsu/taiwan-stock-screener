
"""Automatic TPEx historical backfill plus October 2026 gap repair.

Repair never overwrites existing daily prices or deletes progress.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg
import requests

from tpex_stock_list import get_tpex_stock_codes
from tpex_backfill import (
    HEADERS,
    get_month,
    parse_month,
    store_without_overwriting,
)

REPAIR_MONTH = "2026-10"
REPAIR_FIRST_DAY = date(2026, 10, 1)
REFERENCE_DAY = "2026-10-28"
TARGET_DAYS = (
    "2026-10-19",
    "2026-10-21",
    "2026-10-27",
    "2026-10-29",
    "2026-10-30",
)


def taiwan_today():
    return datetime.now(ZoneInfo("Asia/Taipei")).date()


def get_target_months(count):
    today = taiwan_today()
    year, month = today.year, today.month
    months = []

    for _ in range(count):
        key = f"{year:04d}-{month:02d}"

        if key != today.strftime("%Y-%m"):
            months.append(key)

        month -= 1

        if month == 0:
            year -= 1
            month = 12

    return months


def get_completed_codes(conn, count):
    months = get_target_months(count)

    if not months:
        return set()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT code
            FROM public.backfill_progress
            WHERE market = 'TPEx'
              AND month = ANY(%s)
            GROUP BY code
            HAVING COUNT(DISTINCT month) = %s
            """,
            (months, len(months)),
        )

        return {
            str(row[0])
            for row in cur.fetchall()
        }


def create_repair_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS
                public.tpex_october_repair_audit (
                    month TEXT NOT NULL,
                    code TEXT NOT NULL,
                    valid_rows INTEGER NOT NULL,
                    inserted_rows INTEGER NOT NULL,
                    checked_at TIMESTAMPTZ NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (month, code)
                )
            """
        )

    conn.commit()


def get_repair_candidates(conn, codes):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT code, date
            FROM public.prices
            WHERE market = 'TPEX'
              AND date = ANY(%s)
              AND code = ANY(%s)
            """,
            ([REFERENCE_DAY, *TARGET_DAYS], codes),
        )

        covered = {
            (str(code), str(day))
            for code, day in cur.fetchall()
        }

        cur.execute(
            """
            SELECT code
            FROM public.tpex_october_repair_audit
            WHERE month = %s
            """,
            (REPAIR_MONTH,),
        )

        checked = {
            str(row[0])
            for row in cur.fetchall()
        }

    baseline = {
        code
        for code, day in covered
        if day == REFERENCE_DAY
    }

    candidates = [
        code
        for code in baseline
        if code not in checked
        and any(
            (code, day) not in covered
            for day in TARGET_DAYS
        )
    ]

    candidates.sort(
        key=lambda code: (
            -sum(
                (code, day) not in covered
                for day in TARGET_DAYS
            ),
            code,
        )
    )

    print(
        f"REPAIR_SCAN baseline={len(baseline)}; "
        f"audited={len(checked)}; "
        f"pending={len(candidates)}",
        flush=True,
    )

    return candidates


def stored_month_dates(conn, code):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT date
            FROM public.prices
            WHERE market = 'TPEX'
              AND code = %s
              AND date >= '2026-10-01'
              AND date < '2026-11-01'
            """,
            (code,),
        )

        return {
            str(row[0])
            for row in cur.fetchall()
        }


def save_audit(
    conn,
    code,
    valid_count,
    inserted_count,
):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.tpex_october_repair_audit
                (month, code, valid_rows, inserted_rows)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (month, code)
            DO UPDATE SET
                valid_rows = EXCLUDED.valid_rows,
                inserted_rows = EXCLUDED.inserted_rows,
                checked_at = CURRENT_TIMESTAMP
            """,
            (
                REPAIR_MONTH,
                code,
                valid_count,
                inserted_count,
            ),
        )

    conn.commit()


def repair_october(
    conn,
    selected,
    write,
    delay,
):
    inserted_total = 0
    failures = []

    with requests.Session() as session:
        session.headers.update(HEADERS)

        for code in selected:
            try:
                payload = get_month(
                    session,
                    code,
                    REPAIR_FIRST_DAY,
                )

                records = parse_month(
                    payload,
                    code,
                    REPAIR_MONTH,
                )

                stored = stored_month_dates(
                    conn,
                    code,
                )

                source_dates = {
                    str(row[0])
                    for row in records
                }

                # Do not accept an incomplete response
                # or mark it as audited.
                if (
                    not source_dates
                    or not stored.issubset(source_dates)
                ):
                    raise ValueError(
                        "官方月資料未涵蓋既有日期: "
                        f"source={len(source_dates)}, "
                        f"stored={len(stored)}"
                    )

                if write:
                    inserted = store_without_overwriting(
                        conn,
                        records,
                    )

                    save_audit(
                        conn,
                        code,
                        len(records),
                        inserted,
                    )

                    inserted_total += inserted

                    print(
                        f"REPAIR_OK {code} "
                        f"{REPAIR_MONTH}: "
                        f"new={inserted}; "
                        f"valid={len(records)}",
                        flush=True,
                    )

                else:
                    print(
                        f"REPAIR_DRY_RUN {code}: "
                        f"valid={len(records)}; "
                        f"stored={len(stored)}",
                        flush=True,
                    )

            except Exception as exc:
                conn.rollback()

                failures.append(
                    f"{code}: {exc}"
                )

                print(
                    f"REPAIR_FAILED {code}: {exc}",
                    flush=True,
                )

            time.sleep(
                max(1.5, delay)
            )

    return inserted_total, failures


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
        "--delay",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    if not 1 <= args.batch_size <= 20:
        raise ValueError(
            "每批股票必須介於 1 至 20 檔"
        )

    if not 2 <= args.months <= 12:
        raise ValueError(
            "月份必須介於 2 至 12"
        )

    if args.delay < 0:
        raise ValueError(
            "delay 不可為負數"
        )

    database_url = os.environ.get(
        "DATABASE_URL"
    )

    if not database_url:
        raise RuntimeError(
            "缺少 DATABASE_URL"
        )

    codes = list(
        dict.fromkeys(
            str(code)
            for code in get_tpex_stock_codes()
            if re.fullmatch(
                r"[1-9]\d{3}",
                str(code),
            )
        )
    )

    print(
        f"TPEx 股票清單：{len(codes)} 檔",
        flush=True,
    )

    with psycopg.connect(database_url) as conn:
        create_repair_table(conn)

        candidates = get_repair_candidates(
            conn,
            codes,
        )

        repair_slots = (
            args.batch_size + 1
        ) // 2

        repair_selected = candidates[
            :repair_slots
        ]

        completed = get_completed_codes(
            conn,
            args.months,
        )

        history_pending = [
            code
            for code in codes
            if code not in completed
            and code not in repair_selected
        ]

        history_selected = history_pending[
            :args.batch_size - len(repair_selected)
        ]

        print(
            "REPAIR_SELECTED: "
            + (
                ",".join(repair_selected)
                or "-"
            ),
            flush=True,
        )

        print(
            "HISTORY_SELECTED: "
            + (
                ",".join(history_selected)
                or "-"
            ),
            flush=True,
        )

        inserted, failures = repair_october(
            conn,
            repair_selected,
            args.write,
            args.delay,
        )

    if history_selected:
        command = [
            sys.executable,
            "tpex_backfill.py",
            "--codes",
            ",".join(history_selected),
            "--months",
            str(args.months),
            "--delay",
            str(args.delay),
        ]

        if args.write:
            command.append("--write")

        try:
            subprocess.run(
                command,
                check=True,
            )

        except subprocess.CalledProcessError as exc:
            failures.append(
                f"HISTORY: {exc}"
            )

    print(
        "AUTO_TPEX_SUMMARY "
        f"repair_codes={len(repair_selected)}; "
        f"repair_new_rows={inserted}; "
        f"history_codes={len(history_selected)}; "
        f"failures={len(failures)}",
        flush=True,
    )

    for detail in failures:
        print(
            f"FAILED_DETAIL {detail}",
            flush=True,
        )

    if failures:
        raise RuntimeError(
            "TPEx 歷史回補部分失敗，"
            "成功資料已保留"
        )


if __name__ == "__main__":
    main()
