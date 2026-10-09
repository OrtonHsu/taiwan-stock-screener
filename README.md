# 台股盤後選股 V1.1（保守的可執行升級版）

## 已實作
- 保留原本 MA20、MA60、20日突破、量比、成交金額及排名策略。
- `history.py` 透過 TWSE 官方單股單月 `STOCK_DAY` 歷史端點，下載指定上市股票過去8個月的日期可驗證行情。
- SQLite 本機測試；設定 `DATABASE_URL` 時改用持久化 PostgreSQL。
- GitHub Actions 台灣時間平日17:30左右執行；若 GitHub 排程延遲，仍依官方資料日期存檔，避免誤標日期。
- `date,market,code` 唯一鍵去重；資料庫操作失敗回滾。

## 尚未實作／限制
- **不是全市場回補。** 為避免對官方網站大量請求，預設只追蹤5檔上市股票，最多30檔。若要全市場，需另行設計官方全市場歷史逐日批次來源及速率控制。
- **上櫃歷史自動回補尚未實作。** 可以用經核實 CSV 匯入。
- 尚未驗證官方端點在實際部署環境的可用性；若欄位變更，Action 日誌會出現警告。
- 官方月資料在月底前可能尚未完成；程式只記錄回傳的實際日期。
- 未提供除權息還原、三大法人、股票類型辨識、回測、獲利保證。
- `TWSE_CODES` 請只設定上市普通股代號；本版未自動驗證股票類型。
- 請先確認官方資料使用條款及個人用途範圍，勿未經許可轉售／再散布。

## 部署步驟（沿用現有 GitHub Repo）
1. 建立受管理的 PostgreSQL 資料庫（例如 Supabase）。取得 **PostgreSQL 連線字串**（`postgresql://...`）；注意選擇支援 IPv4 的 pooler（如環境需要）。
2. GitHub 儲存庫 `Settings > Secrets and variables > Actions > New repository secret`，新增 `DATABASE_URL`，值為連線字串。切勿把密碼寫入程式或公開截圖。
3. GitHub `Settings > Secrets and variables > Actions > Variables` 可選擇新增 `TWSE_CODES`，例如 `2330,2317,2454,2303,2882`。預設5檔，最多30檔。
4. Streamlit Cloud App 管理頁 `Settings > Secrets` 輸入 `DATABASE_URL = "postgresql://..."`（TOML 格式，特殊字元依 TOML 規則跳脫）。與 GitHub Actions 使用**同一個**資料庫。
5. 將本資料夾全部檔案（包含隱藏的 `.github/workflows/update.yml`）上傳至原本的 `OrtonHsu/taiwan-stock-screener` 儲存庫 `main` 分支，取代同名檔案。
6. GitHub 儲存庫 `Actions > Update TWSE History > Run workflow` 手動執行一次。執行結果請看 Actions 日誌；官方可能限制頻率，執行需數分鐘。
7. 返回 Streamlit 網站重新整理，確認最新日期與交易日數。之後平日自動排程執行；GitHub 排程可能延遲或停用，需定期查看執行紀錄。

## 測試
```
pip install -r requirements.txt
pytest -q
```
本機沒有 `DATABASE_URL` 時使用 `data/quotes.sqlite3`，只適合本機測試，不是雲端永久保存。

## 風險與安全
- 不要把 PostgreSQL 密碼上傳 GitHub，也不要把 `.env`、SQLite 資料庫、金鑰提交到公開 repo。
- PostgreSQL 連線需 SSL/TLS（依供應商要求）。建議資料庫帳號只授予所需資料表權限。
- 每日排程會重抓最近8個月，每檔8次請求，預設40次；可在初始化後將 workflow 的 `--months 8` 改成 `--months 1`，降低流量。若要自動補缺月，應另做缺口偵測。
- GitHub Actions 的排程不保證精確時間；若當日官方資料尚未公布，之後應重跑補齊。
