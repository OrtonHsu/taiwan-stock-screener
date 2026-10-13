
"""Taiwan stock screener V1.2 - personal read-only dashboard."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from screener import rank_stocks
from storage import Store


st.set_page_config(
    page_title="台股盤後選股 V1.2",
    page_icon="📈",
    layout="wide",
)

st.title("📈 台股盤後選股 V1.2")
st.caption(
    "個人投資研究工具｜每日盤後觀察｜"
    "不提供自動下單｜技術分數不代表上漲機率"
)

st.warning(
    "⚠️ 上市及上櫃歷史資料仍持續回補。"
    "目前候選股僅來自歷史資料足夠且符合條件的股票，"
    "尚不能視為完整全市場前 10 名。"
)


# ---------------------------------
# Sidebar settings
# ---------------------------------

with st.sidebar:
    st.header("⚙️ 選股設定")

    min_history = st.slider(
        "最低歷史交易日數",
        min_value=65,
        max_value=180,
        value=65,
        step=5,
    )

    turnover = st.number_input(
        "20日平均成交金額下限（萬元）",
        min_value=1000,
        max_value=100000,
        value=5000,
        step=1000,
    )

    vol_ratio = st.slider(
        "當日量／20日均量",
        min_value=1.0,
        max_value=4.0,
        value=1.5,
        step=0.1,
    )

    st.divider()

    st.subheader("📌 系統說明")
    st.info(
        "每日行情由 GitHub Actions 自動更新。\n\n"
        "歷史資料由背景排程持續回補。\n\n"
        "本網站只讀取資料庫，不提供上傳或手動寫入功能。"
    )


# ---------------------------------
# Database connection
# ---------------------------------

try:
    url = os.getenv("DATABASE_URL", "")

    if not url:
        try:
            url = st.secrets.get("DATABASE_URL", "")
        except Exception:
            pass

    db = Store(url=url)

    try:
        prices = db.load()
    finally:
        db.close()

except Exception as exc:
    st.error(f"資料庫讀取失敗：{exc}")
    st.stop()


if prices.empty:
    st.info(
        "資料庫目前沒有股票行情。"
        "請先確認 GitHub Actions 的每日更新執行紀錄。"
    )
    st.stop()


# ---------------------------------
# Data preparation
# ---------------------------------

prices = prices.copy()

prices["date"] = prices["date"].astype(str)
prices["market"] = prices["market"].astype(str)
prices["code"] = prices["code"].astype(str)

prices = prices.dropna(
    subset=["date", "market", "code"]
)

latest = prices["date"].max()

today = datetime.now(
    ZoneInfo("Asia/Taipei")
).date().isoformat()

latest_prices = prices.loc[
    prices["date"] == latest
].copy()

latest_count = (
    latest_prices[["market", "code"]]
    .drop_duplicates()
    .shape[0]
)

market_counts = (
    latest_prices.groupby("market")["code"]
    .nunique()
    .to_dict()
)

trading_days = prices["date"].nunique()


# ---------------------------------
# Overview
# ---------------------------------

st.subheader("📊 最新行情概況")

col1, col2, col3 = st.columns(3)

col1.metric(
    "最新資料日期",
    latest,
)

col2.metric(
    "累積交易日期數",
    f"{trading_days:,}",
)

col3.metric(
    "最新資料股票數",
    f"{latest_count:,}",
)

col4, col5 = st.columns(2)

col4.metric(
    "上市股票",
    f"{market_counts.get('TWSE', 0):,}",
)

col5.metric(
    "上櫃股票",
    f"{market_counts.get('TPEX', 0):,}",
)

if latest != today:
    st.info(
        f"目前最新行情日期是 {latest}。"
        "休市日或盤後更新尚未完成時，"
        "最新日期可能不是今天。"
    )


# ---------------------------------
# History coverage
# ---------------------------------

st.subheader("🗂️ 歷史資料完整度")

history_counts = (
    prices.groupby(["market", "code"])["date"]
    .nunique()
    .reset_index(name="history_days")
)

latest_codes = latest_prices[
    ["market", "code"]
].drop_duplicates()

coverage = latest_codes.merge(
    history_counts,
    on=["market", "code"],
    how="left",
)

qualified_history = int(
    (coverage["history_days"] >= min_history).sum()
)

total_latest = len(coverage)

coverage_pct = (
    qualified_history / total_latest * 100
    if total_latest > 0
    else 0.0
)

st.progress(
    min(1.0, coverage_pct / 100.0)
)

st.write(
    f"最新交易日有 **{qualified_history:,} / "
    f"{total_latest:,}** 檔股票，"
    f"累積至少 **{min_history}** 個歷史交易日。"
)

st.caption(
    f"歷史筆數達標比例：{coverage_pct:.1f}%。"
    "這是資料筆數覆蓋率，不代表交易日連續，"
    "也不代表資料已完成除權息調整。"
)

if qualified_history < total_latest:
    st.warning(
        "部分股票歷史資料尚未達標，"
        "因此暫時無法參與選股排名。"
    )


# ---------------------------------
# Screening
# ---------------------------------

st.divider()

st.subheader("🏆 最新交易日候選股票")

st.caption(
    "條件：多頭均線、突破前20日高點、"
    "成交量放大、流動性達標。"
    "符合條件者依技術分數排序，最多顯示10檔。"
)

try:
    results = rank_stocks(
        prices,
        min_history=min_history,
        min_amount=turnover * 10000,
        vol_ratio=vol_ratio,
    )

except Exception as exc:
    st.error(f"選股計算失敗：{exc}")
    st.stop()


if not results.empty:
    results = results.loc[
        results["date"].astype(str) == latest
    ].copy()

    results = results.sort_values(
        "score",
        ascending=False,
    ).head(10)


if results.empty:
    st.info(
        "目前沒有符合條件的股票。"
        "可能是歷史資料不足，"
        "或市場上沒有符合突破與量能條件的標的。"
        "系統不會為了湊滿10檔而放寬條件。"
    )

else:
    st.success(
        f"本次找到 {len(results)} 檔候選股票。"
    )

    # Compact table
    display_columns = [
        "market",
        "code",
        "name",
        "close",
        "score",
        "volume_ratio",
        "ma20",
        "ma60",
        "breakout_ref",
        "history_days",
    ]

    table = results[
        display_columns
    ].rename(
        columns={
            "market": "市場",
            "code": "代號",
            "name": "名稱",
            "close": "收盤價",
            "score": "技術分數",
            "volume_ratio": "量比",
            "ma20": "20日均線",
            "ma60": "60日均線",
            "breakout_ref": "突破參考價",
            "history_days": "歷史交易日",
        }
    )

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        label="📥 下載前10名候選股 CSV",
        data=results.to_csv(
            index=False
        ).encode("utf-8-sig"),
        file_name=f"screen_{latest}.csv",
        mime="text/csv",
    )

    # Mobile-friendly details
    st.subheader("📱 個股詳細資訊")

    st.caption(
        "點擊股票即可展開詳細資訊。"
        "下列價格為技術參考，不是買進指令。"
    )

    for _, stock in results.iterrows():

        market_name = (
            "上市"
            if stock["market"] == "TWSE"
            else "上櫃"
        )

        heading = (
            f"{market_name} "
            f"{stock['code']} "
            f"{stock['name']}｜"
            f"{stock['score']:.1f}分"
        )

        with st.expander(heading):

            a, b = st.columns(2)

            a.metric(
                "最新收盤價",
                f"{stock['close']:.2f}",
            )

            b.metric(
                "成交量比",
                f"{stock['volume_ratio']:.2f} 倍",
            )

            c, d = st.columns(2)

            c.metric(
                "20日均線",
                f"{stock['ma20']:.2f}",
            )

            d.metric(
                "60日均線",
                f"{stock['ma60']:.2f}",
            )

            st.write(
                f"**前20日突破參考價：** "
                f"{stock['breakout_ref']:.2f}"
            )

            st.write(
                f"**20日平均成交金額：** "
                f"{stock['avg_turnover'] / 10000:,.0f} 萬元"
            )

            st.write(
                f"**歷史交易日數：** "
                f"{stock['history_days']} 日"
            )

            st.caption(
                "技術分數用於比較符合條件的程度，"
                "不代表勝率。"
                "目前尚未計算隔日進場價、"
                "停損價與預期報酬。"
            )


# ---------------------------------
# Notes
# ---------------------------------

st.divider()

with st.expander("📚 資料來源與使用限制"):
    st.write(
        "每日上市行情：TWSE 官方資料，"
        "含已驗證的 MI_INDEX 備援來源。"
    )

    st.write(
        "每日上櫃行情：TPEx 官方資料。"
    )

    st.write(
        "上市及上櫃歷史行情："
        "由 GitHub Actions 排程持續回補。"
    )

    st.write(
        "目前選股依據為歷史收盤價、"
        "成交股數及成交金額，"
        "尚未納入法人籌碼、財報基本面、"
        "除權息還原或交易成本回測。"
    )

    st.write(
        "候選股票不等於買進建議。"
        "進場前仍需評估當日價格、"
        "市場風險、部位大小及停損規劃。"
    )

st.caption(
    "台股盤後選股 V1.2｜"
    "個人研究用途｜"
    "無自動交易功能"
)
