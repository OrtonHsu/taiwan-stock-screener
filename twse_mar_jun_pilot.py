"""V2 phase 1: manually verify TWSE March-June 2026 monthly history.

Default is dry-run. Writes only on explicit --write.
Existing prices never overwritten; incomplete/unknown months never marked done.
"""
import argparse
import os
import re
import time
from datetime import date

import psycopg
import requests

from market_backfill import (
    create_progress_table,
    fetch_month,
    parse_month,
    verified_before_listing,
)

MONTHS = tuple(date(2026, m, 1) for m in (3, 4, 5, 6))
INSERT_SQL = """
    INSERT INTO public.prices
      (date, market, code, name, close, volume, amount)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (date, market, code) DO NOTHING
"""
PROGRESS_SQL = """
    INSERT INTO public.backfill_progress (market, code, month, row_count)
    VALUES ('TWSE', %s, %s, %s)
    ON CONFLICT (market, code, month)
    DO UPDATE SET row_count = EXCLUDED.row_count,
                  completed_at = CURRENT_TIMESTAMP
"""


def previously_completed(conn, code, month):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT row_count FROM public.backfill_progress
               WHERE market='TWSE' AND code=%s AND month=%s""",
            (code, month),
        )
        row = cur.fetchone()
        if not row:
            return False
        expected_rows = int(row[0])
        if expected_rows == 0:
            return False  # Zero-row results need independent verification.
        cur.execute(
            """SELECT COUNT(*) FROM public.prices
               WHERE market='TWSE' AND code=%s AND LEFT(date, 7)=%s""",
            (code, month),
        )
        actual_rows = int(cur.fetchone()[0])
        return actual_rows >= expected_rows


def write_month(conn, code, month, rows):
    """Insert prices and mark progress in the same transaction."""
    try:
        with conn.cursor() as cur:
            cur.executemany(INSERT_SQL, rows)
            inserted = cur.rowcount
            cur.execute(PROGRESS_SQL, (code, month, len(rows)))
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", default="2330,2317,2454,2303,2882")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    codes = list(dict.fromkeys(x.strip() for x in args.codes.split(",") if x.strip()))
    if not 1 <= len(codes) <= 5 or not all(re.fullmatch(r"[1-9]\d{3}", x) for x in codes):
        parser.error("Pilot requires 1 to 5 four-digit stock codes.")
    if not 1.5 <= args.delay <= 30:
        parser.error("Delay must be between 1.5 and 30 seconds.")

    db_url = os.getenv("DATABASE_URL")
    if args.write and not db_url:
        raise RuntimeError("Missing DATABASE_URL")
    print(f"MODE={'WRITE' if args.write else 'DRY_RUN'}; codes={','.join(codes)}", flush=True)

    conn = None
    inserted_total = 0
    checked = 0
    errors = []
    try:
        if args.write:
            conn = psycopg.connect(db_url, connect_timeout=15)
            create_progress_table(conn)

        with requests.Session() as session:
            for code in codes:
                for month in MONTHS:
                    month_key = month.strftime("%Y-%m")
                    label = f"{code} {month_key}"
                    if verified_before_listing(code, month):
                        print(f"PRE_LISTING {label}: not queried", flush=True)
                        continue
                    if conn and previously_completed(conn, code, month_key):
                        print(f"SKIPPED {label}: verified stored count", flush=True)
                        continue

                    try:
                        payload = fetch_month(session, code, month)
                        rows = parse_month(payload, code, month_key)
                        if len(rows) < 10:
                            raise ValueError(
                                f"Only {len(rows)} valid quotes; pilot refuses "
                                "to certify a potentially partial month."
                            )
                        inserted = write_month(conn, code, month_key, rows) if conn else 0
                        inserted_total += inserted
                        checked += 1
                        print(
                            f"{'WRITTEN' if conn else 'CHECK'} {label}: "
                            f"valid={len(rows)} inserted={inserted}",
                            flush=True,
                        )
                    except Exception as exc:
                        if conn:
                            conn.rollback()
                        errors.append(f"{label}: {exc}")
                        print(f"FAILED {label}: {exc}", flush=True)
                    time.sleep(args.delay)
    finally:
        if conn:
            conn.close()

    print(
        f"TWSE_PILOT_SUMMARY checked={checked}; inserted={inserted_total}; "
        f"failures={len(errors)}; mode={'WRITE' if args.write else 'DRY_RUN'}",
        flush=True,
    )
    for error in errors:
        print(f"FAILED_DETAIL {error}", flush=True)
    if errors:
        raise RuntimeError("Pilot had unresolved month(s); successful writes kept.")


if __name__ == "__main__":
    main()
