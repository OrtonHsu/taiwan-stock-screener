
"""取得上櫃股票候選清單。"""

import re
import requests

URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}


def get_tpex_stock_codes():
    response = requests.get(
        URL,
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list) or len(data) < 100:
        raise RuntimeError("上櫃股票清單資料異常")

    codes = sorted({
        str(item.get("SecuritiesCompanyCode", "")).strip()
        for item in data
        if isinstance(item, dict)
        and re.fullmatch(
            r"[1-9]\d{3}",
            str(item.get("SecuritiesCompanyCode", "")).strip(),
        )
    })

    if len(codes) < 100:
        raise RuntimeError("取得的上櫃股票代號數量異常")

    return codes


if __name__ == "__main__":
    codes = get_tpex_stock_codes()
    print(f"取得 {len(codes)} 個上櫃證券候選代號")
    print(",".join(codes[:20]))
