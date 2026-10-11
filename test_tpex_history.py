
"""診斷櫃買中心歷史行情資料來源，不寫入資料庫。"""

import re
from urllib.parse import urljoin

import requests

BASE = "https://www.tpex.org.tw"
PAGE = BASE + "/zh-tw/mainboard/trading/info/stock-pricing.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}

KEYWORDS = (
    "stock-pricing",
    "st43",
    "download",
    "csv",
    "api/",
    "historical",
    "stock_price",
    "stockprice",
)


def inspect_text(label, content):
    print(f"\n===== {label} =====")
    print("內容長度：", len(content))

    lines = content.splitlines()
    matches = []

    for number, line in enumerate(lines, 1):
        if any(word in line.lower() for word in KEYWORDS):
            cleaned = line.strip()
            if len(cleaned) > 400:
                cleaned = cleaned[:400] + " ..."

            matches.append((number, cleaned))

    print("關鍵字匹配行數：", len(matches))

    for number, line in matches[:20]:
        print(f"第 {number} 行：{line}")


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    response = session.get(
        PAGE,
        params={"code": "1240"},
        timeout=45,
    )
    response.raise_for_status()

    print("網頁 HTTP 狀態：", response.status_code)
    inspect_text("HTML 網頁", response.text)

    scripts = re.findall(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']',
        response.text,
        flags=re.IGNORECASE,
    )

    scripts = list(dict.fromkeys(scripts))

    print("\n找到 JavaScript 檔案數量：", len(scripts))

    # 限制檢查數量，避免一次下載過多檔案
    for index, src in enumerate(scripts[:20], 1):
        url = urljoin(response.url, src)

        # 只檢查櫃買中心自己的 JavaScript
        if not url.startswith(BASE + "/"):
            continue

        print(f"\n檢查 JS {index}：{url}")

        try:
            js_response = session.get(url, timeout=30)
            print("HTTP 狀態：", js_response.status_code)

            if js_response.status_code != 200:
                continue

            # 避免下載或輸出過大的檔案
            if len(js_response.content) > 2_000_000:
                print("檔案太大，略過")
                continue

            inspect_text(
                f"JavaScript {index}",
                js_response.text,
            )

        except requests.RequestException as error:
            print("讀取失敗：", str(error)[:200])

    print("\n診斷完成，沒有寫入資料庫。")
    print("注意：找到關鍵字不代表已確認 API 可用。")


if __name__ == "__main__":
    main()
