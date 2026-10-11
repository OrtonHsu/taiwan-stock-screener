
"""TPEx monthly history backfill with optional database writes."""

import argparse
import os
import re
import time
from datetime import date

import requests

from screener import num
from storage import Store

API = (
    "https://www.tpex.org.tw/www/zh-tw/"
    "afterTrading/tradingStock"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": (
        "https://www.tpex.org.tw/zh-tw/mainboard/"
        "trading/info/stock-pricing.html"
    ),
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
    if not isinstance(payload, dict):
        raise ValueError("官方回傳格式異常")

    if str(payload.get("stat", "")).lower() != "ok":
        raise ValueError(
            f"官方狀態異常：{payload.get('stat')}"
        )

    tables = payload.get("tables")

    if not isinstance(tables, list) or not tables:
        raise ValueError("官方沒有提供行情表格")

    table = tables[0]
    fields = table.get("fields", [])
    source_rows = table.get("data", [])

    required = (
        "日 期",
        "成交張數",
        "成交仟元",
        "收盤",
    )

    positions = {}

    for field in required:
        if field not in fields:
            raise ValueError(
                f"缺少欄位：{field}；實際：{fields}"
            )
        positions[field] = fields.index(field)

    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError("官方沒有提供每日行情")

    name = str(payload.get("name") or code).strip()

    records = []
    seen_dates = set()

    for item in source_rows:
        try:
            raw_date = str(
                item[positions["日 期"]]
            ).strip()

            match = re.fullmatch(
                r"(\d{2,3})/(\d{1,2})/(\d{1,2})",
                raw_date,
            )

            if not match:
                raise ValueError(
                    f"日期格式異常：{raw_date}"
                )

            year, month, day = map(
                int, match.groups()
            )

            trading_day = date(
                year + 1911, month, day
            )

            if (
                trading_day.strftime("%Y-%m")
                != expected_month
            ):
                raise ValueError(
                    f"日期不屬於查詢月份：{raw_date}"
                )

            if trading_day in seen_dates:
                raise ValueError(
                    f"官方資料日期重複：{raw_date}"
                )

            seen_dates.add(trading_day)

            close = num(
                item[positions["收盤"]]
            )

            if close is None or close <= 0:
                print(
                    f"SKIP {code} {raw_date}: "
                    "無有效收盤價"
                )
                continue

            volume = num(
                item[positions["成交張數"]]
            )

            amount = num(
                item[positions["成交仟元"]]
            )

            if volume is None or amount is None:
                raise ValueError(
                    f"成交量或金額異常：{raw_date}"
                )

            if volume < 0 or amount < 0:
                raise ValueError(
                    f"成交量或金額為負數：{raw_date}"
                )

            records.append((
                trading_day.isoformat(),
                "TPEx",
                code,
                name,
                close,
                volume * 1000,
                amount * 1000,
            ))

        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError(
                f"資料驗證失敗：{exc}"
            ) from exc

    if not records:
        raise ValueError("沒有有效收盤價紀錄")

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
            WHERE market = 'TPEx'
              AND code = %s
              AND month = %s
        """, (code, month))
        return cur.fetchone() is not None


def mark_completed(conn, code, month, row_count):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO backfill_progress
                (market, code, month, row_count)
            VALUES ('TPEx', %s, %s, %s)
            ON CONFLICT (market, code, month)
            DO UPDATE SET
                row_count = EXCLUDED.row_count,
                completed_at = CURRENT_TIMESTAMP
        """, (code, month, row_count))
    conn.commit()


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--codes",
        default="1240",
    )

    parser.add_argument(
        "--months",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help="正式寫入 Supabase；未指定則只測試",
    )

    args = parser.parse_args()

    codes = list(dict.fromkeys(
        code.strip()
        for code in args.codes.split(",")
        if code.strip()
    ))

    if not 1 <= len(codes) <= 20:
        raise ValueError("每批只允許 1 至 20 檔")

    if not all(
        re.fullmatch(r"\d{4}", code)
        for code in codes
    ):
        raise ValueError("股票代號必須為四碼數字")

    if not 1 <= args.months <= 12:
        raise ValueError("月份必須介於 1 至 12")

    if args.write and not os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "正式寫入模式缺少 DATABASE_URL"
        )

    total = 0
    written = 0
    skipped = 0
    failures = []

    current_month = date.today().strftime("%Y-%m")

    progress_conn = None
    db = None

    try:
        if args.write:
            import psycopg

            progress_conn = psycopg.connect(
                os.environ["DATABASE_URL"]
            )
            create_progress_table(progress_conn)
            db = Store()

        with requests.Session() as session:
            session.headers.update(HEADERS)

            for code in codes:
                for month in months_back(args.months):
                    month_key = month.strftime("%Y-%m")
                    label = f"{code} {month_key}"

                    if (
                        args.write
                        and month_key != current_month
                        and is_completed(
                            progress_conn, code, month_key
                        )
                    ):
                        skipped += 1
                        print(
                            f"SKIPPED {label}: 已完成"
                        )
                        continue

                    params = {
                        "code": code,
                        "date": month.strftime("%Y/%m/%d"),
                        "id": "",
                        "response": "json",
                    }

                    try:
                        response = session.get(
                            API,
                            params=params,
                            timeout=45,
                        )
                        response.raise_for_status()

                        records = parse_month(
                            response.json(),
                            code,
                            month_key,
                        )

                        total += len(records)

                        if args.write:
                            count = db.store(records)
                            written += count

                            if month_key != current_month:
                                mark_completed(
                                    progress_conn,
                                    code,
                                    month_key,
                                    len(records),
                                )

                            print(
                                f"WRITTEN {label}: "
                                f"{count} rows"
                            )
                        else:
                            print(
                                f"OK {label}: "
                                f"{len(records)} valid rows"
                            )

                            for row in records[:3]:
                                print("SAMPLE:", row)

                    except Exception as exc:
                        failures.append(
                            f"{label}: {exc}"
                        )
                        print(
                            f"FAILED {label}: {exc}"
                        )

                    time.sleep(
                        max(1.5, args.delay)
                    )

    finally:
        if db is not None:
            db.close()

        if progress_conn is not None:
            progress_conn.close()

    print("\n===== 執行總結 =====")
    print(
        "模式：",
        "WRITE" if args.write else "DRY RUN",
    )
    print("有效資料筆數：", total)
    print("寫入筆數：", written)
    print("跳過月份數：", skipped)
    print("失敗月份數：", len(failures))

    if failures:
        raise RuntimeError(
            "部分月份處理失敗，請查看執行紀錄"
        )


if __name__ == "__main__":
    main()
