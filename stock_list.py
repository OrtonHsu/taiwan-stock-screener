
"""取得臺灣證券交易所上市普通股清單。"""

import re

import requests

URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"


def get_twse_stock_codes():
    response = requests.get(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list) or len(data) < 100:
        raise RuntimeError("上市股票清單資料異常")

    codes = sorted({
        str(item.get("Code", "")).strip()
        for item in data
        if isinstance(item, dict)
        and re.fullmatch(
            r"\d{4}",
            str(item.get("Code", "")).strip(),
        )
    })

    if len(codes) < 100:
        raise RuntimeError("取得的上市股票代號數量異常")

    return codes


if __name__ == "__main__":
    codes = get_twse_stock_codes()
    print(f"取得 {len(codes)} 個四碼上市證券代號")
    print(",".join(codes[:20]))
