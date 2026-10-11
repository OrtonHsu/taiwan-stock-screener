
"""測試櫃買中心上櫃個股歷史行情，不寫入資料庫。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/"
    "daily_trading_info/st43_result.php"
)

now = datetime.now(ZoneInfo("Asia/Taipei"))
roc_year = now.year - 1911

# 先測試前一個完整月份
year = now.year
month = now.month - 1
if month == 0:
    year -= 1
    month = 12

period = f"{year - 1911}/{month:02d}"

params = {
    "l": "zh-tw",
    "d": period,
    "stkno": "1240",
}

print(f"測試股票：1240")
print(f"測試月份：{period}")

response = requests.get(
    URL,
    params=params,
    headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.tpex.org.tw/",
    },
    timeout=45,
)
response.raise_for_status()

print(f"HTTP 狀態：{response.status_code}")
print(f"回傳格式：{response.headers.get('Content-Type')}")

try:
    data = response.json()
except ValueError:
    print("官方未回傳 JSON，內容前 500 字：")
    print(response.text[:500])
    raise

if not isinstance(data, dict):
    raise ValueError("官方回傳格式不是預期的物件")

print("官方欄位：", list(data.keys()))
print("資料狀態：", data.get("stat"))
print("欄位名稱：", data.get("fields"))

rows = data.get("aaData", data.get("data", []))
if not isinstance(rows, list) or not rows:
    raise ValueError("沒有取得歷史行情，需確認 API 格式")

print(f"成功取得 {len(rows)} 筆歷史行情")
print("第一筆資料：", rows[0])
