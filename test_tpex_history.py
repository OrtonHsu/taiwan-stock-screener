
"""驗證櫃買中心歷史行情 JSON 欄位，不寫入資料庫。"""

import json
import requests

API = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"

PARAMS = {
    "code": "1240",
    "date": "2026/09/01",
    "id": "",
    "response": "json",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": (
        "https://www.tpex.org.tw/zh-tw/mainboard/"
        "trading/info/stock-pricing.html"
    ),
}


def main():
    response = requests.get(
        API,
        params=PARAMS,
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()

    payload = response.json()

    print("===== API 回應 =====")
    print("HTTP:", response.status_code)
    print("資料類型:", type(payload).__name__)

    if not isinstance(payload, dict):
        print("回傳資料:")
        print(json.dumps(
            payload, ensure_ascii=False, indent=2
        )[:5000])
        return

    print("\n===== 所有頂層欄位 =====")

    for key, value in payload.items():
        print(key, "=>", type(value).__name__)

        if isinstance(value, list):
            print("筆數:", len(value))

            for index, item in enumerate(value[:3], 1):
                print(
                    f"第 {index} 筆:",
                    json.dumps(item, ensure_ascii=False),
                )

        elif isinstance(value, dict):
            print(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    indent=2,
                )[:1500]
            )

        else:
            print("內容:", str(value)[:500])

    print("\n===== 資料驗證 =====")

    for key in ("stat", "status", "date", "code", "name"):
        if key in payload:
            print(key, "=", payload[key])

    print("\n驗證完成。")
    print("沒有修改任何資料庫。")


if __name__ == "__main__":
    main()
