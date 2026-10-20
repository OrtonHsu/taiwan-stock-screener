
"""取得上櫃股票候選清單，支援自動重試與資料驗證。"""

import re
import time

import requests


URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_daily_close_quotes"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

MAX_RETRIES = 5
TIMEOUT = 45
MIN_CODES = 500


def download_stock_list(session):
    """下載並檢查官方股票清單。"""

    response = session.get(
        URL,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise ValueError(
            "官方 API 回傳格式異常：不是陣列"
        )

    if len(data) < MIN_CODES:
        raise ValueError(
            f"官方資料筆數不足：{len(data)}"
        )

    codes = set()

    for item in data:
        if not isinstance(item, dict):
            continue

        code = str(
            item.get(
                "SecuritiesCompanyCode",
                "",
            )
        ).strip()

        if re.fullmatch(r"[1-9]\d{3}", code):
            codes.add(code)

    codes = sorted(codes)

    if len(codes) < MIN_CODES:
        raise ValueError(
            "有效上櫃股票候選代號不足："
            f"{len(codes)} 檔"
        )

    return codes


def get_tpex_stock_codes():
    """下載上櫃股票清單，失敗時最多重試5次。"""

    last_error = None

    with requests.Session() as session:
        session.headers.update(HEADERS)

        for attempt in range(
            1,
            MAX_RETRIES + 1,
        ):
            try:
                codes = download_stock_list(
                    session
                )

                print(
                    "TPEX STOCK LIST OK: "
                    f"{len(codes)} codes",
                    flush=True,
                )

                return codes

            except (
                requests.exceptions.RequestException,
                ValueError,
            ) as exc:
                last_error = exc

                print(
                    "TPEX STOCK LIST RETRY "
                    f"{attempt}/{MAX_RETRIES}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

                if attempt == MAX_RETRIES:
                    break

                # Backoff: 3, 6, 9, 12 seconds.
                wait_seconds = attempt * 3

                time.sleep(wait_seconds)

    raise RuntimeError(
        "上櫃股票清單下載失敗，"
        f"已嘗試 {MAX_RETRIES} 次。"
        f"最後錯誤：{last_error}"
    )


if __name__ == "__main__":
    codes = get_tpex_stock_codes()

    print(
        f"取得 {len(codes)} "
        "個上櫃證券候選代號"
    )

    print(
        ",".join(codes[:20])
    )
