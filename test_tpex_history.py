
"""檢查櫃買中心歷史行情頁面的查詢與下載設定。"""

import re
from html import unescape
from urllib.parse import urljoin

import requests

PAGE = (
    "https://www.tpex.org.tw/zh-tw/mainboard/"
    "trading/info/stock-pricing.html"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,*/*",
}

KEYWORDS = (
    "stock-pricing",
    "download",
    "csv",
    "query",
    "export",
    "form",
    "action",
    "api",
)


def clean(text):
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:500]


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

    print("HTTP 狀態：", response.status_code)
    print("網頁網址：", response.url)
    print("HTML 長度：", len(html))

    print("\n===== 查詢表單 =====")

    forms = re.findall(
        r"<form\b.*?</form\s*>",
        html,
        flags=re.I | re.S,
    )

    print("表單數量：", len(forms))

    for index, form in enumerate(forms[:10], 1):
        opening = re.search(r"<form\b[^>]*>", form, re.I | re.S)

        if opening:
            print(f"表單 {index}：", clean(opening.group()))

        controls = re.findall(
            r"<(?:input|select|button)\b[^>]*>",
            form,
            flags=re.I | re.S,
        )

        for control in controls[:25]:
            print("欄位：", clean(control))

    print("\n===== 下載連結 =====")

    links = re.findall(
        r"<a\b[^>]*>.*?</a\s*>",
        html,
        flags=re.I | re.S,
    )

    matched_links = [
        link for link in links
        if any(word in link.lower() for word in KEYWORDS)
    ]

    print("候選連結數量：", len(matched_links))

    for link in matched_links[:15]:
        print(clean(link))

    print("\n===== 網頁內嵌設定 =====")

    scripts = re.findall(
        r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script\s*>",
        html,
        flags=re.I | re.S,
    )

    print("內嵌程式區塊：", len(scripts))

    for index, script in enumerate(scripts, 1):
        lines = script.splitlines()

        for number, line in enumerate(lines, 1):
            if any(word in line.lower() for word in KEYWORDS):
                print(
                    f"區塊 {index} 第 {number} 行：",
                    clean(line),
                )

    print("\n===== HTML 資料屬性 =====")

    attributes = re.findall(
        r'(?:data-[\w-]+|(?:href|action))'
        r'\s*=\s*["\'][^"\']+["\']',
        html,
        flags=re.I,
    )

    candidates = [
        item for item in attributes
        if any(word in item.lower() for word in KEYWORDS)
    ]

    for item in candidates[:30]:
        print(clean(item))

    print("\n診斷完成。")
    print("沒有下載歷史資料，也沒有寫入資料庫。")


if __name__ == "__main__":
    main()
