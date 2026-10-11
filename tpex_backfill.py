
"""TPEx monthly historical price backfill.

Default: dry-run only. No database writes.
"""

import argparse
import re
import time
from datetime import date

import requests

from screener import num

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
        raise ValueError("官方回傳不是 JSON 物件")

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

    required = {
        "日 期": "date",
        "成交張數": "volume",
        "成交仟元": "amount",
        "收盤": "close",
    }

    positions = {}

    for field in required:
        if field not in fields:
            raise ValueError(
                f"找不到必要欄位：{field}；實際欄位：{fields}"
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
                    f"重複日期：{raw_date}"
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

    args = parser.parse_args()

    codes = list(dict.fromkeys(
        code.strip()
        for code in args.codes.split(",")
        if code.strip()
    ))

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
            "月份必須介於 1 至 12"
        )

    total = 0
    failures = []

    with requests.Session() as session:
        session.headers.update(HEADERS)

        for code in codes:
            for month in months_back(args.months):
                label = (
                    f"{code} "
                    f"{month.strftime('%Y-%m')}"
                )

                params = {
                    "code": code,
                    "date": month.strftime(
                        "%Y/%m/%d"
                    ),
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
                        month.strftime("%Y-%m"),
                    )

                    total += len(records)

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

    print("\n===== 測試總結 =====")
    print("有效資料筆數：", total)
    print("失敗月份數：", len(failures))
    print("模式：DRY RUN")
    print("沒有寫入 Supabase。")

    if failures:
        raise RuntimeError(
            "部分月份驗證失敗，請查看紀錄"
        )


if __name__ == "__main__":
    main()
