
"""測試櫃買中心上櫃股票月歷史行情 API。"""

import json
import requests

API = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": (
        "https://www.tpex.org.tw/zh-tw/mainboard/"
        "trading/info/stock-pricing.html"
    ),
}

# 同時測試民國年月與西元年月。
TESTS = [
    {"code": "1240", "date": "115/09"},
    {"code": "1240", "date": "2026/09"},
    {"code": "1240", "date": "115/09/01"},
    {"code": "1240", "date": "2026-09"},
]


def inspect_response(response):
    print("HTTP 狀態：", response.status_code)
    print("最終網址：", response.url)
    print("資料類型：", response.headers.get("Content-Type"))
    print("回傳長度：", len(response.content))

    try:
        data = response.json()
    except ValueError:
        print("不是 JSON，回傳前 600 字：")
        print(response.text[:600])
        return

    if isinstance(data, dict):
        print("JSON 欄位：", list(data.keys())[:30])

        for key, value in data.items():
            if isinstance(value, list):
                print(f"{key} 筆數：", len(value))
                print(
                    "前兩筆：",
                    json.dumps(
                        value[:2],
                        ensure_ascii=False,
                    )[:1800],
                )
            elif isinstance(value, dict):
                print(
                    f"{key} 內容：",
                    json.dumps(
                        value,
                        ensure_ascii=False,
                    )[:600],
                )
            else:
                print(f"{key}：", str(value)[:300])

    elif isinstance(data, list):
        print("JSON 陣列筆數：", len(data))
        print(
            "前兩筆：",
            json.dumps(
                data[:2],
                ensure_ascii=False,
            )[:1800],
        )
    else:
        print("JSON 內容：", str(data)[:600])


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    for index, params in enumerate(TESTS, 1):
        print("\n" + "=" * 50)
        print(f"第 {index} 組參數：", params)

        try:
            response = session.get(
                API,
                params=params,
                timeout=45,
            )
            inspect_response(response)
        except requests.RequestException as error:
            print("連線失敗：", str(error)[:500])

    print("\n測試完成，沒有寫入資料庫。")


if __name__ == "__main__":
    main()
