
"""Taiwan stock screener V1.3 - personal risk dashboard."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from screener import rank_stocks
from storage import Store


st.set_page_config(
    page_title="台股盤後選股 V1.3",
    page_icon="📈",
    layout="wide",
)

st.title("📈 台股盤後選股 V1.3")

st.caption(
    "個人投資研究工具｜盤後選股｜進場風險觀察｜"
    "無自動下單｜分數不代表上漲機率"
)

st.warning(
    "⚠️ 上市與上櫃歷史資料仍持續回補。"
    "目前候選股只來自資料足夠的股票，"
    "尚不能視為完整全市場最佳前10名。"
)


# ---------------------------------
# Sidebar
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

    st.info(
        "每日行情由 GitHub Actions 更新。\n\n"
        "歷史資料持續自動回補。\n\n"
        "網站只讀取行情，不提供公開寫入功能。"
    )


# ---------------------------------
# Database
# ---------------------------------

try:
    url = os.getenv("DATABASE_URL", "")

    if not url:
        try:
            url = st.secrets.get(
                "DATABASE_URL", ""
            )
        except Exception:
            pass

    db = Store(url=url)

    try:
        prices = db.load()
    finally:
        db.close()

except Exception as exc:
    st.error(
        f"資料庫讀取失敗：{exc}"
    )
    st.stop()


if prices.empty:
    st.info(
        "資料庫目前沒有行情資料，"
        "請檢查 GitHub Actions 更新紀錄。"
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

c1, c2, c3 = st.columns(3)

c1.metric(
    "最新資料日期",
    latest,
)

c2.metric(
    "累積交易日期",
    f"{trading_days:,}",
)

c3.metric(
    "最新行情股票",
    f"{latest_count:,}",
)

c4, c5 = st.columns(2)

c4.metric(
    "上市股票",
    f"{market_counts.get('TWSE', 0):,}",
)

c5.metric(
    "上櫃股票",
    f"{market_counts.get('TPEX', 0):,}",
)

other_count = latest_count - (
    market_counts.get("TWSE", 0)
    + market_counts.get("TPEX", 0)
)

if other_count:
    st.warning(
        f"最新行情有 {other_count} 筆"
        "不屬於 TWSE/TPEX 的市場標記，"
        "建議後續檢查資料來源。"
    )

if latest != today:
    st.info(
        f"最新資料日期為 {latest}。"
        "休市日、收盤前或資料尚未更新時，"
        "日期可能不是今天。"
    )


# ---------------------------------
# History coverage
# ---------------------------------

st.divider()

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
    (
        coverage["history_days"] >= min_history
    ).sum()
)

total_latest = len(coverage)

coverage_pct = (
    qualified_history / total_latest * 100
    if total_latest
    else 0
)

st.progress(
    min(1.0, coverage_pct / 100)
)

st.write(
    f"已有 **{qualified_history:,} / "
    f"{total_latest:,}** 檔股票，"
    f"累積至少 **{min_history}** 個歷史交易日。"
)

st.caption(
    f"歷史筆數達標比例：{coverage_pct:.1f}%。"
    "此比例不代表交易日連續，"
    "也不代表完成除權息調整。"
)

if qualified_history < total_latest:
    st.warning(
        "部分股票歷史資料尚未達標，"
        "暫時無法參加選股排名。"
    )


# ---------------------------------
# Screening calculation
# ---------------------------------

st.divider()

st.subheader("🏆 最新交易日候選股票")

st.caption(
    "依多頭趨勢、突破前20日高點、"
    "成交量放大與流動性條件篩選。"
    "符合條件者按技術分數排序，最多10檔。"
)

try:
    results = rank_stocks(
        prices,
        min_history=min_history,
        min_amount=turnover * 10000,
        vol_ratio=vol_ratio,
    )

except Exception as exc:
    st.error(
        f"選股計算失敗：{exc}"
    )
    st.stop()


if not results.empty:
    results = results.loc[
        results["date"].astype(str) == latest
    ].copy()

    results = results.sort_values(
        ["score", "risk_pct"],
        ascending=[False, True],
    ).head(10)


if results.empty:
    st.info(
        "目前沒有符合條件的股票。"
        "可能是資料不足，或沒有股票符合"
        "趨勢、突破及量能條件。"
        "系統不會為了湊滿10檔而放寬條件。"
    )

else:
    st.success(
        f"本次找到 {len(results)} 檔候選股票。"
    )

    display_columns = [
        "market",
        "code",
        "name",
        "close",
        "score",
        "risk_level",
        "extension_pct",
        "risk_pct",
        "volume_ratio",
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
            "risk_level": "追高風險",
            "extension_pct": "20日乖離%",
            "risk_pct": "支撐距離%",
            "volume_ratio": "量比",
        }
    )

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "📥 下載候選股與風險分析 CSV",
        data=results.to_csv(
            index=False
        ).encode("utf-8-sig"),
        file_name=f"screen_v13_{latest}.csv",
        mime="text/csv",
    )

    # ---------------------------------
    # Risk overview
    # ---------------------------------

    st.subheader("🛡️ 追高風險概況")

    risk_counts = (
        results["risk_level"]
        .value_counts()
        .to_dict()
    )

    r1, r2, r3 = st.columns(3)

    r1.metric(
        "低風險標籤",
        risk_counts.get("低", 0),
    )

    r2.metric(
        "中風險標籤",
        risk_counts.get("中", 0),
    )

    r3.metric(
        "高風險標籤",
        risk_counts.get("高", 0),
    )

    st.caption(
        "低、中、高為短線追價條件分類，"
        "不代表股票的真實虧損機率。"
        "低風險標籤也不代表適合立即買進。"
    )

    # ---------------------------------
    # Individual stock detail
    # ---------------------------------

    st.divider()

    st.subheader("📱 個股進場風險分析")

    st.caption(
        "點擊股票可展開技術數據與風險資訊。"
        "所有價位均屬研究參考。"
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
            f"{stock['score']:.1f}分｜"
            f"追高風險：{stock['risk_level']}"
        )

        with st.expander(heading):

            st.markdown(
                f"### {stock['code']} "
                f"{stock['name']}"
            )

            a, b = st.columns(2)

            a.metric(
                "最新收盤價",
                f"{stock['close']:.2f}",
            )

            b.metric(
                "技術分數",
                f"{stock['score']:.1f}",
            )

            c, d = st.columns(2)

            c.metric(
                "成交量比",
                f"{stock['volume_ratio']:.2f} 倍",
            )

            d.metric(
                "20日均線乖離",
                f"{stock['extension_pct']:.2f}%",
            )

            st.divider()

            st.markdown("#### 📉 技術參考價")

            e, f = st.columns(2)

            e.metric(
                "20日均線",
                f"{stock['ma20']:.2f}",
            )

            f.metric(
                "60日均線",
                f"{stock['ma60']:.2f}",
            )

            g, h = st.columns(2)

            g.metric(
                "突破參考價",
                f"{stock['breakout_ref']:.2f}",
            )

            h.metric(
                "支撐參考價",
                f"{stock['stop_reference']:.2f}",
            )

            st.metric(
                "收盤價至支撐參考價距離",
                f"{stock['risk_pct']:.2f}%",
            )

            st.divider()

            st.markdown("#### 🚦 隔日追價風險")

            risk_level = stock["risk_level"]
            risk_note = stock["risk_note"]

            if risk_level == "高":
                st.error(
                    f"高追價風險：{risk_note}"
                )

            elif risk_level == "中":
                st.warning(
                    f"中追價風險：{risk_note}"
                )

            else:
                st.info(
                    f"低追價風險標籤：{risk_note}"
                )

            st.write(
                "**隔日追價警戒參考：** "
                f"{stock['watch_ceiling']:.2f} 元"
            )

            st.caption(
                "此參考值以當日收盤價上方2%計算，"
                "只是提醒避免追價的示意門檻，"
                "並非建議買入價或合理價估值。"
            )

            st.write(
                "**近20日平均成交金額：** "
                f"{stock['avg_turnover'] / 10000:,.0f} 萬元"
            )

            st.write(
                "**歷史資料筆數：** "
                f"{stock['history_days']} 日"
            )

            st.caption(
                "支撐參考價是依收盤價、20日均線"
                "與突破位置建立的簡化規則，"
                "不等於可實際成交的停損價。"
                "遇跳空下跌，損失可能超過顯示距離。"
            )


# ---------------------------------
# Risk explanation
# ---------------------------------

st.divider()

with st.expander("📖 風險分級如何計算？"):

    st.write(
        "**20日乖離率：** "
        "最新收盤價相對20日均線高出多少百分比。"
    )

    st.write(
        "**支撐距離：** "
        "最新收盤價到程式估算技術支撐價的距離。"
    )

    st.write(
        "**高追價風險：** "
        "20日乖離率超過12%，"
        "或支撐距離超過8%。"
    )

    st.write(
        "**中追價風險：** "
        "未達高風險條件，但20日乖離率超過7%，"
        "或支撐距離超過5%。"
    )

    st.write(
        "**低追價風險標籤：** "
        "未觸及上述中、高風險門檻。"
        "仍可能面臨重大下跌、基本面或流動性風險。"
    )

    st.write(
        "這些門檻尚未完成歷史回測，"
        "不代表未來報酬或勝率。"
    )


with st.expander("📚 資料來源與使用限制"):

    st.write(
        "上市每日行情來自 TWSE 官方來源，"
        "並具備 MI_INDEX 備援機制。"
    )

    st.write(
        "上櫃每日行情來自 TPEx 官方來源。"
    )

    st.write(
        "歷史資料透過 GitHub Actions 持續回補。"
    )

    st.write(
        "目前尚未完整處理除權息還原、"
        "法人籌碼、基本面、重大公告、"
        "處置股票、交易成本及績效回測。"
    )

    st.write(
        "技術分數和風險分類只適合作為研究起點，"
        "不能取代個別投資判斷。"
    )


st.caption(
    "台股盤後選股 V1.3｜"
    "個人研究使用｜"
    "無自動交易功能"
)
