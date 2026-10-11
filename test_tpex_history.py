
"""第九次測試：驗證櫃買中心歷史行情 API。"""

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

# 2026 年 9 月 = 民國 115 年 9 月
TESTS = [
    {"code": "1240", "date": "115/09", "response": "json"},
    {"code": "1240", "date": "2026/09", "response": "json"},
    {"code": "1240", "date": "2026-09", "response": "json"},
    {"code": "1240", "date": "115/09/01", "response": "json"},
]


def inspect(response):
    print("HTTP 狀態：", response.status_code)
    print("最終網址：", response.url)
    print("資料類型：", response.headers.get("Content-Type"))
    print("回傳大小：", len(response.content))

    try:
        data = response.json()
    except ValueError:
        print("非 JSON 回覆：", response.text[:500])
        return

    if isinstance(data, dict):
        print("JSON 欄位：", list(data.keys()))

        for key, value in data.items():
            if isinstance(value, list):
                print(f"{key} 筆數：", len(value))
                print(
                    "前兩筆：",
                    json.dumps(
                        value[:2],
                        ensure_ascii=False,
                    )[:1500],
                )
            elif isinstance(value, dict):
                print(
                    f"{key}：",
                    json.dumps(
                        value,
                        ensure_ascii=False,
                    )[:500],
                )
            else:
                print(f"{key}：", str(value)[:300])

    elif isinstance(data, list):
        print("資料筆數：", len(data))
        print(
            "前兩筆：",
            json.dumps(
                data[:2],
                ensure_ascii=False,
            )[:1500],
        )
    else:
        print("JSON 內容：", str(data)[:500])


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    for number, params in enumerate(TESTS, 1):
        print("\n" + "=" * 50)
        print(f"第 {number} 組測試：", params)

        try:
            response = session.get(
                API,
                params=params,
                timeout=45,
            )
            inspect(response)
        except requests.RequestException as error:
            print("連線失敗：", str(error)[:500])

    print("\n第九次測試完成。")
    print("沒有寫入 Supabase。")


if __name__ == "__main__":
    main()
