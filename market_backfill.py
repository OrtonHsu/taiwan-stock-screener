
"""Safe TWSE monthly history backfill with progress tracking."""

import argparse
import os
import re
import time
from datetime import date, datetime
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

# Verified first TWSE trading dates.
# Months before these dates have no TWSE listing history.
# Add further entries only after official verification.

FIRST_TWSE_TRADING_DATE = {
    "2237": date(2026, 8, 10),
    "6467": date(2026, 10, 20),
    "7812": date(2026, 9, 22),
    "7855": date(2026, 8, 11),
    "8908": date(2026, 10, 16),
    "7710": date(2026, 10, 27),
}


MAX_RETRIES = 3


def taiwan_today():
    return datetime.now(
        ZoneInfo("Asia/Taipei")
    ).date()


def months_back(count):
    today = taiwan_today()
    year, month = today.year, today.month

    for _ in range(count):
        yield date(year, month, 1)

        month -= 1

        if month == 0:
            year -= 1
            month = 12


def verified_before_listing(code, month):
    first_date = FIRST_TWSE_TRADING_DATE.get(code)

    if first_date is None:
        return False

    first_month = date(
        first_date.year,
        first_date.month,
        1,
    )

    return month < first_month


def parse_month(payload, code, expected_month):
    if not isinstance(payload, dict):
        raise ValueError(
            "官方回傳格式異常：不是 JSON 物件"
        )

    status = payload.get("stat")

    if status != "OK":
        raise ValueError(
            f"官方狀態異常: {status}"
        )

    fields = payload.get("fields", [])

    required = [
        "日期",
        "成交股數",
        "成交金額",
        "收盤價",
    ]

    if not isinstance(fields, list):
        raise ValueError("官方欄位格式異常")

    if not all(
        key in fields
        for key in required
    ):
        raise ValueError(
            f"官方欄位異動: {fields}"
        )

    positions = {
        key: fields.index(key)
        for key in required
    }

    source_rows = payload.get("data", [])

    if (
        not isinstance(source_rows, list)
        or not source_rows
    ):
        raise ValueError(
            "官方沒有提供歷史行情"
        )

    records = []
    seen_dates = set()

    for item in source_rows:
        try:
            if not isinstance(item, (list, tuple)):
                raise ValueError(
                    "官方資料列格式異常"
                )

            raw = str(
                item[positions["日期"]]
            ).strip()

            match = re.fullmatch(
                r"(\d{2,3})/(\d{1,2})/(\d{1,2})",
                raw,
            )

            if not match:
                raise ValueError(
                    f"日期格式異常：{raw}"
                )

            year, month, day = map(
                int,
                match.groups(),
            )

            trading_day = date(
                year + 1911,
                month,
                day,
            )

            if (
                trading_day.strftime("%Y-%m")
                != expected_month
            ):
                raise ValueError(
                    "回傳日期不屬於要求月份"
                )

            if trading_day in seen_dates:
                raise ValueError(
                    "官方資料有重複日期"
                )

            seen_dates.add(trading_day)

            close = num(
                item[positions["收盤價"]]
            )

            volume = num(
                item[positions["成交股數"]]
            )

            amount = num(
                item[positions["成交金額"]]
            )

            if close is None or close <= 0:
                print(
                    f"SKIP {code} {raw}: "
                    "無有效收盤價"
                )
                continue

            if volume is None or amount is None:
                raise ValueError(
                    "成交量或成交金額格式異常："
                    f"{raw}"
                )

            if volume < 0 or amount < 0:
                raise ValueError(
                    "成交量或成交金額不可為負數："
                    f"{raw}"
                )

            records.append(
                (
                    trading_day.isoformat(),
                    "TWSE",
                    code,
                    code,
                    close,
                    volume,
                    amount,
                )
            )

        except (
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "月份資料未完整通過驗證: "
                f"{exc}"
            ) from exc

    return records


def create_progress_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS
            backfill_progress (
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                month TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                completed_at TIMESTAMPTZ NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    market,
                    code,
                    month
                )
            )
            """
        )

    conn.commit()


def is_completed(conn, code, month):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM backfill_progress
            WHERE market = 'TWSE'
              AND code = %s
              AND month = %s
            """,
            (code, month),
        )

        return cur.fetchone() is not None


def mark_completed(
    conn,
    code,
    month,
    row_count,
):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO backfill_progress
                (
                    market,
                    code,
                    month,
                    row_count
                )
            VALUES (
                'TWSE',
                %s,
                %s,
                %s
            )
            ON CONFLICT (
                market,
                code,
                month
            )
            DO UPDATE SET
                row_count = EXCLUDED.row_count,
                completed_at = CURRENT_TIMESTAMP
            """,
            (
                code,
                month,
                row_count,
            ),
        )

    conn.commit()


def fetch_month(
    session,
    code,
    month,
):
    params = {
        "date": month.strftime("%Y%m%d"),
        "stockNo": code,
        "response": "json",
    }

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(
                URL,
                params=params,
                headers=HEADERS,
                timeout=35,
            )

            response.raise_for_status()

            return response.json()

        except (
            requests.exceptions.RequestException,
            ValueError,
        ) as exc:
            last_error = exc

            # Do not retry ordinary client-side HTTP errors.
            if (
                isinstance(
                    exc,
                    requests.exceptions.HTTPError,
                )
                and exc.response is not None
                and 400 <= exc.response.status_code < 500
                and exc.response.status_code != 429
            ):
                break

            if attempt < MAX_RETRIES:
                print(
                    f"RETRY {code} "
                    f"{month.strftime('%Y-%m')} "
                    f"{attempt}/{MAX_RETRIES}: "
                    f"{exc}",
                    flush=True,
                )

                time.sleep(
                    3 * attempt
                )

    raise RuntimeError(
        "官方行情取得失敗，"
        f"已嘗試最多 {MAX_RETRIES} 次: "
        f"{last_error}"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--codes",
        required=True,
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

    args = parser.parse_args()

    codes = list(
        dict.fromkeys(
            code.strip()
            for code in args.codes.split(",")
            if code.strip()
        )
    )

    if not 1 <= len(codes) <= 20:
        raise ValueError(
            "每批只允許 1 至 20 檔股票"
        )

    if not all(
        re.fullmatch(r"\d{4}", code)
        for code in codes
    ):
        raise ValueError(
            "股票代號必須是四碼數字"
        )

    if not 1 <= args.months <= 12:
        raise ValueError(
            "回補月份必須介於 1 至 12"
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

    written = 0
    skipped = 0
    verified_no_data = 0
    failures = []

    current_month = (
        taiwan_today().strftime("%Y-%m")
    )

    with psycopg.connect(
        database_url
    ) as progress_conn:
        create_progress_table(
            progress_conn
        )

        db = Store(url=database_url)

        try:
            with requests.Session() as session:
                for code in codes:
                    for month in months_back(
                        args.months
                    ):
                        month_key = (
                            month.strftime("%Y-%m")
                        )

                        label = (
                            f"{code} {month_key}"
                        )

                        is_current_month = (
                            month_key == current_month
                        )

                        # Historical completed months
                        # do not need to be downloaded again.
                        if (
                            not is_current_month
                            and is_completed(
                                progress_conn,
                                code,
                                month_key,
                            )
                        ):
                            skipped += 1

                            print(
                                f"SKIPPED {label}: "
                                "已完成或已查核",
                                flush=True,
                            )

                            continue

                        # Only explicitly verified
                        # pre-listing periods qualify.
                        if verified_before_listing(
                            code,
                            month,
                        ):
                            mark_completed(
                                progress_conn,
                                code,
                                month_key,
                                0,
                            )

                            verified_no_data += 1

                            print(
                                f"VERIFIED_NO_DATA "
                                f"{label}: "
                                "上市前月份，官方上市"
                                "交易日期已核對",
                                flush=True,
                            )

                            continue

                        try:
                            payload = fetch_month(
                                session,
                                code,
                                month,
                            )

                            rows = parse_month(
                                payload,
                                code,
                                month_key,
                            )

                            count = db.store(rows)

                            written += count

                            # Only closed historical months
                            # can be marked completed.
                            if not is_current_month:
                                mark_completed(
                                    progress_conn,
                                    code,
                                    month_key,
                                    len(rows),
                                )

                            print(
                                f"OK {label}: "
                                f"{len(rows)} rows",
                                flush=True,
                            )

                        except Exception as exc:
                            failures.append(
                                f"{label}: {exc}"
                            )

                            print(
                                f"FAILED {label}: {exc}",
                                flush=True,
                            )

                        time.sleep(
                            max(
                                1.5,
                                args.delay,
                            )
                        )

        finally:
            db.close()

    print(
        "===== TWSE 回補總結 =====",
        flush=True,
    )

    print(
        f"written={written}; "
        f"skipped={skipped}; "
        f"verified_no_data={verified_no_data}; "
        f"failed_requests={len(failures)}",
        flush=True,
    )

    for failure in failures:
        print(
            f"FAILED_DETAIL {failure}",
            flush=True,
        )

    if failures:
        raise RuntimeError(
            "仍有未確認的月份失敗；"
            "成功資料及進度已保留，"
            "請查看 FAILED_DETAIL"
        )


if __name__ == "__main__":
    main()
