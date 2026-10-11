
"""確認櫃買中心歷史行情 API 網址格式，不寫入資料庫。"""

import re
from urllib.parse import urljoin, urlparse

import requests

PAGE = (
    "https://www.tpex.org.tw/zh-tw/mainboard/"
    "trading/info/stock-pricing.html"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}

KEYWORDS = (
    "API_PATTERN",
    "afterTrading/tradingStock",
    "tables.init",
    "data-format",
)


def show_matches(label, content):
    print(f"\n===== {label} =====")

    count = 0

    for keyword in KEYWORDS:
        matches = list(
            re.finditer(
                re.escape(keyword),
                content,
                flags=re.IGNORECASE,
            )
        )

        print(f"{keyword}：找到 {len(matches)} 處")

        for match in matches[:3]:
            start = max(0, match.start() - 350)
            end = min(len(content), match.end() + 450)

            snippet = content[start:end]
            snippet = snippet.replace("\n", " ")
            snippet = re.sub(r"\s+", " ", snippet)

            print("前後程式碼：", snippet[:900])
            count += 1

    print("輸出片段數量：", count)


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    response = session.get(
        PAGE,
        params={"code": "1240"},
        timeout=45,
    )
    response.raise_for_status()

    html = response.text

    print("官方網頁狀態：", response.status_code)
    show_matches("HTML", html)

    scripts = re.findall(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )

    scripts = list(dict.fromkeys(scripts))

    print("\n找到 JS 數量：", len(scripts))

    for index, src in enumerate(scripts, 1):
        url = urljoin(response.url, src)

        if urlparse(url).hostname != "www.tpex.org.tw":
            continue

        try:
            js_response = session.get(
                url,
                timeout=30,
            )
            js_response.raise_for_status()

            if len(js_response.content) > 2_000_000:
                print("略過大型 JS：", url)
                continue

            content = js_response.text

            if not any(
                keyword.lower() in content.lower()
                for keyword in KEYWORDS
            ):
                continue

            print("\nJS 網址：", url)
            show_matches(
                f"JavaScript {index}",
                content,
            )

        except requests.RequestException as error:
            print("讀取失敗：", str(error)[:200])

    print("\n診斷完成。")
    print("沒有寫入資料庫。")
    print("尚未驗證歷史行情 API 回傳資料。")


if __name__ == "__main__":
    main()
