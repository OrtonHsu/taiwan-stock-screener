
"""Bounded, resumable TWSE monthly history backfill."""
import argparse
import re
import time
from datetime import date

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
    records = []

    for item in payload.get("data", []):
        raw = str(item[positions["日期"]]).strip()
        match = re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", raw)
        if not match:
            continue

        year, month, day = map(int, match.groups())
        try:
            trading_day = date(year + 1911, month, day)
        except ValueError:
            continue

        if trading_day.strftime("%Y-%m") != expected_month:
            raise ValueError("官方回傳日期不屬於要求月份")

        close = num(item[positions["收盤價"]])
        volume = num(item[positions["成交股數"]])
        amount = num(item[positions["成交金額"]])

        if any(value is None for value in (close, volume, amount)):
            continue
        if close <= 0 or volume <= 0 or amount < 0:
            continue

        records.append((
            trading_day.isoformat(), "TWSE", code, code,
            close, volume, amount
        ))

    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", required=True)
    parser.add_argument("--months", type=int, default=4)
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()

    codes = list(dict.fromkeys(
        code.strip() for code in args.codes.split(",") if code.strip()
    ))

    if not 1 <= len(codes) <= 20:
        raise ValueError("每批只允許 1 至 20 檔股票")
    if not all(re.fullmatch(r"\d{4}", code) for code in codes):
        raise ValueError("股票代號必須是四碼數字")
    if not 1 <= args.months <= 12:
        raise ValueError("回補月份必須介於 1 至 12")

    db = Store()
    written = 0
    failures = []

    try:
        with requests.Session() as session:
            for code in codes:
                for month in months_back(args.months):
                    label = f"{code} {month:%Y-%m}"
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
                            response.json(), code, month.strftime("%Y-%m")
                        )
                        if not rows:
                            raise ValueError("沒有有效歷史行情")
                        written += db.store(rows)
                        print(f"OK {label}: {len(rows)} rows")
                    except Exception as exc:
                        failures.append(f"{label}: {exc}")
                        print(f"FAILED {label}: {exc}")

                    time.sleep(max(1.5, args.delay))
    finally:
        db.close()

    print(f"written={written}; failed_requests={len(failures)}")
    if failures:
        raise RuntimeError("部分月份回補失敗，請檢查上方紀錄")


if __name__ == "__main__":
    main()
