
"""檢查櫃買中心歷史行情查詢參數，不寫入資料庫。"""

import re
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

WORDS = (
    "tables.init",
    "afterTrading/tradingStock",
    "tables-form",
    "date",
    "code",
    "parameter",
    "params",
    "serialize",
    "query",
    "request",
)


def show_context(title, content, keywords, radius=400):
    print("\n=====", title, "=====")

    for keyword in keywords:
        matches = list(
            re.finditer(
                re.escape(keyword),
                content,
                flags=re.IGNORECASE,
            )
        )

        print(f"{keyword}: {len(matches)} 處")

        for match in matches[:3]:
            start = max(0, match.start() - radius)
            end = min(len(content), match.end() + radius)
            snippet = re.sub(
                r"\s+", " ", content[start:end]
            )
            print("內容:", snippet[:1000])


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

    print("網頁 HTTP:", response.status_code)

    forms = re.findall(
        r"<form\b.*?</form\s*>",
        html,
        flags=re.I | re.S,
    )

    print("\n===== 完整表單設定 =====")

    for form in forms:
        if "tables-form" not in form:
            continue

        for tag in re.findall(
            r"<(?:form|input|select|option|button)\b[^>]*>",
            form,
            flags=re.I | re.S,
        ):
            print(re.sub(r"\s+", " ", tag)[:700])

    scripts = re.findall(
        r"<script\b[^>]*>(.*?)</script\s*>",
        html,
        flags=re.I | re.S,
    )

    for script in scripts:
        if "afterTrading/tradingStock" in script:
            show_context(
                "歷史行情初始化設定",
                script,
                WORDS,
                radius=800,
            )

    sources = re.findall(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)',
        html,
        flags=re.I,
    )

    for src in dict.fromkeys(sources):
        url = urljoin(response.url, src)

        if not url.startswith(
            "https://www.tpex.org.tw/"
        ):
            continue

        if not any(
            word in url.lower()
            for word in ("tables.js", "main.js", "global.js")
        ):
            continue

        try:
            result = session.get(url, timeout=30)
            result.raise_for_status()

            print("\nJS:", url)

            show_context(
                "表格 API 參數組合",
                result.text,
                (
                    "API_PATTERN",
                    "serialize",
                    "URLSearchParams",
                    "FormData",
                    "params",
                    "query",
                    "request",
                    "data-format",
                ),
                radius=250,
            )

        except requests.RequestException as error:
            print("JS 讀取失敗:", str(error)[:200])

    print("\n診斷完成，沒有寫入資料庫。")


if __name__ == "__main__":
    main()
