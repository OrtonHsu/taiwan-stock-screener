
"""第十次測試：櫃買中心歷史行情 GET 與 POST 驗證。"""

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

TESTS = [
    (
        "GET：西元完整日期，加 id",
        "GET",
        {
            "code": "1240",
            "date": "2026/09/01",
            "id": "",
            "response": "json",
        },
    ),
    (
        "GET：民國完整日期，加 id",
        "GET",
        {
            "code": "1240",
            "date": "115/09/01",
            "id": "",
            "response": "json",
        },
    ),
    (
        "POST：西元完整日期",
        "POST",
        {
            "code": "1240",
            "date": "2026/09/01",
            "id": "",
            "response": "json",
        },
    ),
    (
        "POST：民國完整日期",
        "POST",
        {
            "code": "1240",
            "date": "115/09/01",
            "id": "",
            "response": "json",
        },
    ),
]


def inspect_response(response):
    print("HTTP 狀態：", response.status_code)
    print("資料類型：", response.headers.get("Content-Type"))
    print("回傳大小：", len(response.content))

    try:
        data = response.json()
    except ValueError:
        print("非 JSON，內容前 500 字：")
        print(response.text[:500])
        return

    if isinstance(data, dict):
        print("JSON 欄位：", list(data.keys())[:25])

        for key, value in data.items():
            if isinstance(value, list):
                print(f"{key}：{len(value)} 筆")
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
            json.dumps(data[:2], ensure_ascii=False)[:1500],
        )
    else:
        print("回傳內容：", str(data)[:500])


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    for number, (label, method, params) in enumerate(TESTS, 1):
        print("\n" + "=" * 50)
        print(f"第 {number} 組：{label}")
        print("請求方式：", method)
        print("查詢參數：", params)

        try:
            if method == "POST":
                response = session.post(
                    API,
                    data=params,
                    timeout=45,
                )
            else:
                response = session.get(
                    API,
                    params=params,
                    timeout=45,
                )

            inspect_response(response)

        except requests.RequestException as error:
            print("請求失敗：", str(error)[:500])

    print("\n第十次測試完成。")
    print("沒有寫入 Supabase。")


if __name__ == "__main__":
    main()
