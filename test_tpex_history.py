
"""診斷櫃買中心新版歷史行情頁面，不寫入資料庫。"""

import re
import requests

BASE = "https://www.tpex.org.tw"
PAGE = (
    BASE
    + "/zh-tw/mainboard/trading/info/"
    + "stock-pricing.html"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/json,*/*",
}

def main():
    with requests.Session() as session:
        response = session.get(
            PAGE,
            params={"code": "1240"},
            headers=HEADERS,
            timeout=45,
        )
        response.raise_for_status()

        print("HTTP 狀態：", response.status_code)
        print(
            "回傳格式：",
            response.headers.get("Content-Type"),
        )
        print("最終網址：", response.url)
        print("網頁長度：", len(response.content))

        html = response.text

        # 找出網頁中可能使用的資料接口
        patterns = [
            r'["\']([^"\']*stock-pricing[^"\']*)["\']',
            r'["\']([^"\']*\.csv[^"\']*)["\']',
            r'["\']([^"\']*\.json[^"\']*)["\']',
            r'["\']([^"\']*/api/[^"\']*)["\']',
        ]

        found = set()

        for pattern in patterns:
            for match in re.findall(
                pattern, html, flags=re.IGNORECASE
            ):
                if len(match) < 300:
                    found.add(match)

        print("可能的資料接口：")
        for item in sorted(found)[:30]:
            print(item)

        print("找到候選接口數量：", len(found))

        if not found:
            print("網頁未直接提供接口，需進一步分析 JS")

        print("診斷完成，沒有寫入資料庫")


if __name__ == "__main__":
    main()
