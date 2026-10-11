
"""搜尋櫃買中心歷史行情 API 線索，不寫入資料庫。"""

import re
from urllib.parse import urljoin, urlparse

import requests

PAGE = (
    "https://www.tpex.org.tw/zh-tw/mainboard/"
    "trading/info/stock-pricing.html"
)

KEYWORDS = (
    "stock-pricing",
    "stock_pricing",
    "st43",
    "download",
    "csv",
    "history",
    "historical",
    "api/",
    "api?",
    "ajax",
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}


def get_text(session, url):
    response = session.get(url, timeout=35)
    response.raise_for_status()
    return response.text


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    html = get_text(session, PAGE + "?code=1240")

    scripts = re.findall(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )

    scripts = list(dict.fromkeys(scripts))

    print("找到 JS 檔案數量：", len(scripts))

    for index, src in enumerate(scripts, 1):
        url = urljoin(PAGE, src)

        if urlparse(url).hostname != "www.tpex.org.tw":
            continue

        print(f"\n===== JS {index} =====")
        print("檔案網址：", url)

        try:
            content = get_text(session, url)
        except requests.RequestException as error:
            print("下載失敗：", str(error)[:150])
            continue

        if len(content) > 2_000_000:
            print("檔案過大，略過")
            continue

        lines = content.splitlines()
        matched = []

        for line_number, line in enumerate(lines, 1):
            lower = line.lower()

            if any(word in lower for word in KEYWORDS):
                matched.append((line_number, line.strip()))

        print("符合關鍵字行數：", len(matched))

        for line_number, line in matched[:12]:
            print(f"第 {line_number} 行：")
            print(line[:600])

        # 額外尋找可能的 API 路徑
        paths = re.findall(
            r'["\']([^"\']{3,250})["\']',
            content,
        )

        candidates = sorted({
            path
            for path in paths
            if any(word in path.lower() for word in KEYWORDS)
            and (
                "/" in path
                or ".csv" in path.lower()
            )
        })

        if candidates:
            print("可能的資料路徑：")

            for path in candidates[:20]:
                print(path[:250])

    print("\n診斷完成，沒有寫入資料庫。")
    print("本次僅搜尋線索，尚未驗證下載接口。")


if __name__ == "__main__":
    main()
