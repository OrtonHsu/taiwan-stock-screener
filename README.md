# 台股盤後選股系統 V1.0（iPhone 雲端網頁版）

本版是**可執行的研究原型**，不是已驗證獲利的交易系統。沒有自動下單，也沒有 AI 預測。

## 功能
- 使用證交所 `STOCK_DAY_ALL` 與櫃買中心 `tpex_mainboard_daily_close_quotes` 最新每日快照。
- SQLite 儲存已取得的日線資料；亦可匯入**來源及日期已核對**的歷史 CSV。
- MA20、MA60、前20日高點突破、成交量放大、成交金額、60日均線斜率篩選。
- 顯示前10名、技術評分及 CSV 下載。**分數不是上漲機率。**
- 交易日資料不足65筆時不產生訊號。

## 限制與重要風險
1. 官方 OpenAPI 最新行情快照可能**不含可驗證的資料日期**。程式要求操作者核對交易日，且僅在台灣時間平日16:00後手動抓取；休市、特殊休市、資料尚未更新仍可能發生錯誤。正式自動化前需加入官方日期驗證。
2. **Streamlit Community Cloud 的本地 SQLite 不保證永久保存**，重新部署或休眠可能失去歷史資料。因此此部署僅供展示／手動測試；要每天自動更新且可靠保存，必須另接 PostgreSQL/Supabase 等持久化資料庫與 GitHub Actions/排程服務。本 ZIP 未宣稱已完成無人值守自動更新。
3. 兩個官方端點的欄位可能變更。因執行環境無外部網路，無法在製作時即時驗證 JSON 格式；若 API 失敗會顯示錯誤而非假資料。
4. 沒有回補完整歷史行情；需要每天累積至少65個交易日，或先匯入已核對的 CSV。請勿將官方當日快照重複標記為過去日期。
5. V1 不含三大法人、基本面、注意／處置股排除、除權息還原、回測。上櫃端點需依實際欄位核對解析結果。
6. 台股部分4碼商品不一定是普通股，應在實盤決策前額外加入證券類型名單驗證。

## 本機安裝
需要 Python 3.11+：
```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell
# .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## 部署到 iPhone 可瀏覽的雲端
1. 建立 GitHub 儲存庫，將本資料夾所有檔案上傳（不要上傳私人資料或 secrets）。
2. 前往 https://share.streamlit.io/ 登入 GitHub，建立新 App，選擇儲存庫、main 分支、入口 `app.py`。
3. 部署後取得 `*.streamlit.app` 網址，使用 iPhone Safari 開啟並加入主畫面。
4. 初期可用 CSV 上傳測試，但因雲端儲存非永久，不要依賴這個版本自動保存多年行情。
5. 若要**真正每天自動更新**，下一階段應配置永久資料庫、排程、官方資料日期驗證、更新失敗通知與資料品質稽核。

## 歷史 CSV 格式
UTF-8 CSV 欄位：`date,market,code,name,close,volume,amount`；date 為 YYYY-MM-DD，market 為 TWSE/TPEX，volume 為股數，amount 為新台幣元。檔案中的歷史行情應來自可核對的合法來源，不能自行捏造。

## 測試
```bash
pip install pytest
python -m pytest -q
```
測試僅覆蓋基本解析、資料庫及不足歷史的防護；不等於官方連線整合測試或策略有效性驗證。
