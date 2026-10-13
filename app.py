
"""Taiwan stock screener V1.4 - industry heat dashboard."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from screener import rank_stocks
from storage import Store
from industry_heat import (
    get_industries,
    calculate_industry_heat,
)


st.set_page_config(
    page_title="台股盤後選股 V1.4",
    page_icon="📈",
    layout="wide",
)

st.title("📈 台股盤後選股 V1.4")

st.caption(
    "個人投資研究｜技術選股｜產業熱度｜"
    "追高風險分析｜無自動下單"
)

st.warning(
    "上市及上櫃歷史資料仍在自動回補。"
    "產業熱度資料未達完整度門檻時，"
    "會顯示「資料不足」。"
    "目前排名尚非完整全市場前10名。"
)


# ----------------------------------------
# Screening settings
# ----------------------------------------

with st.sidebar:
    st.header("⚙️ 選股設定")

    min_history = st.slider(
        "最低歷史交易日數",
        65, 180, 65, 5,
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
        1.0, 4.0, 1.5, 0.1,
    )

    st.divider()

    st.info(
        "行情及歷史資料由 GitHub Actions "
        "自動更新至 Supabase。"
        "本網站不提供行情寫入或自動下單。"
    )


# ----------------------------------------
# Database
# ----------------------------------------

try:
    database_url = os.getenv("DATABASE_URL", "")

    if not database_url:
        try:
            database_url = st.secrets.get(
                "DATABASE_URL", ""
            )
        except Exception:
            pass

    db = Store(url=database_url)

    try:
        prices = db.load()
    finally:
        db.close()

except Exception as exc:
    st.error(f"資料庫讀取失敗：{exc}")
    st.stop()


if prices.empty:
    st.info("資料庫尚無股票行情。")
    st.stop()


prices = prices.copy()

for column in ("date", "market", "code"):
    prices[column] = prices[column].astype(str)

latest = prices["date"].max()

today = datetime.now(
    ZoneInfo("Asia/Taipei")
).date().isoformat()

latest_prices = prices[
    prices["date"] == latest
].copy()

latest_stocks = latest_prices[
    ["market", "code"]
].drop_duplicates()

latest_count = len(latest_stocks)

market_counts = (
    latest_prices.groupby("market")["code"]
    .nunique()
    .to_dict()
)

trading_days = prices["date"].nunique()


# ----------------------------------------
# Official industry classification
# ----------------------------------------

@st.cache_data(
    ttl=3600,
    show_spinner=False,
)
def load_industry_profiles():
    return get_industries()


industry_profiles = pd.DataFrame()
industry_heat = pd.DataFrame()
industry_error = None

with st.spinner("正在讀取官方產業分類..."):
    try:
        industry_profiles = load_industry_profiles()
    except Exception as exc:
        industry_error = str(exc)


if not industry_profiles.empty:
    with st.spinner("正在計算產業熱度..."):
        try:
            industry_heat = calculate_industry_heat(
                prices,
                industry_profiles,
            )
        except Exception as exc:
            industry_error = str(exc)


# ----------------------------------------
# Latest market overview
# ----------------------------------------

st.subheader("📊 最新行情概況")

a, b, c = st.columns(3)

a.metric("最新資料日期", latest)
b.metric("累積交易日期", f"{trading_days:,}")
c.metric("最新行情股票", f"{latest_count:,}")

a, b = st.columns(2)

a.metric(
    "上市股票",
    f"{market_counts.get('TWSE', 0):,}",
)

b.metric(
    "上櫃股票",
    f"{market_counts.get('TPEX', 0):,}",
)

other_count = latest_count - (
    market_counts.get("TWSE", 0)
    + market_counts.get("TPEX", 0)
)

if other_count > 0:
    st.warning(
        f"最新行情有 {other_count} 筆"
        "非 TWSE/TPEX 市場標記，"
        "需後續核對資料來源。"
    )

if latest != today:
    st.info(
        f"最新行情日期為 {latest}。"
        "可能因休市或資料尚未更新。"
    )


# ----------------------------------------
# History coverage
# ----------------------------------------

st.divider()
st.subheader("🗂️ 歷史資料完整度")

history_counts = (
    prices.groupby(["market", "code"])["date"]
    .nunique()
    .reset_index(name="history_days")
)

coverage = latest_stocks.merge(
    history_counts,
    on=["market", "code"],
    how="left",
)

qualified = int(
    (coverage["history_days"] >= min_history).sum()
)

coverage_pct = (
    qualified / latest_count * 100
    if latest_count
    else 0
)

st.progress(
    min(1.0, coverage_pct / 100)
)

st.write(
    f"已有 **{qualified:,} / {latest_count:,}** "
    f"檔股票累積至少 **{min_history}** "
    "個歷史交易日。"
)

st.caption(
    f"歷史筆數達標比例：{coverage_pct:.1f}%。"
    "不代表資料已完成除權息還原。"
)


# ----------------------------------------
# Industry heat overview
# ----------------------------------------

st.divider()
st.subheader("🔥 產業熱度指數")

st.caption(
    "依產業成交金額升溫、上漲家數、"
    "相對市場強弱與成交集中度計算。"
    "採0～100百分制，並非資金淨流入比例"
    "或未來上漲機率。"
)

if industry_error:
    st.warning(
        "官方產業資料或熱度計算暫時無法使用。"
        "不影響原本的個股選股功能。"
    )

if industry_profiles.empty:
    st.info(
        "目前無法取得產業分類，"
        "請稍後重新整理。"
    )

else:
    industry_total = (
        industry_profiles["industry"].nunique()
    )

    if industry_heat.empty:
        valid_heat = pd.DataFrame()
    else:
        valid_heat = industry_heat[
            industry_heat["status"] == "可計算"
        ].copy()

    h1, h2 = st.columns(2)

    h1.metric(
        "官方產業種類",
        industry_total,
    )

    h2.metric(
        "已有熱度分數產業",
        len(valid_heat),
    )

    if valid_heat.empty:
        st.info(
            "目前產業歷史資料覆蓋率不足，"
            "產業熱度暫時顯示「資料不足」。"
            "資料持續回補後會重新計算。"
        )

    else:
        heat_table = valid_heat[
            [
                "industry",
                "industry_heat",
                "coverage_pct",
            ]
        ].copy()

        heat_table["industry_heat"] = (
            heat_table["industry_heat"]
            .map(lambda x: f"{x:.1f}%")
        )

        heat_table = heat_table.rename(
            columns={
                "industry": "產業",
                "industry_heat": "產業熱度",
                "coverage_pct": "資料覆蓋率%",
            }
        )

        st.dataframe(
            heat_table,
            use_container_width=True,
            hide_index=True,
        )


# ----------------------------------------
# Stock screening
# ----------------------------------------

st.divider()
st.subheader("🏆 最新交易日候選股票")

st.caption(
    "依多頭趨勢、突破前20日高點、"
    "成交量及流動性篩選。"
    "依技術分數排序，最多10檔。"
    "產業熱度暫不影響原本排名。"
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
    results = results[
        results["date"].astype(str) == latest
    ].copy()

    results = (
        results.sort_values(
            ["score", "risk_pct"],
            ascending=[False, True],
        )
        .head(10)
        .copy()
    )


# ----------------------------------------
# Attach industries and heat
# ----------------------------------------

if not results.empty:
    if not industry_profiles.empty:
        profile_columns = industry_profiles[
            ["market", "code", "industry"]
        ].drop_duplicates(
            ["market", "code"]
        )

        results = results.merge(
            profile_columns,
            on=["market", "code"],
            how="left",
        )

    else:
        results["industry"] = None

    if not industry_heat.empty:
        heat_columns = industry_heat[
            ["industry", "industry_heat"]
        ].drop_duplicates("industry")

        results = results.merge(
            heat_columns,
            on="industry",
            how="left",
        )

    else:
        results["industry_heat"] = None

    results["industry_display"] = (
        results["industry"]
        .fillna("未分類")
    )

    results["heat_display"] = (
        results["industry_heat"].apply(
            lambda value:
            f"{float(value):.1f}%"
            if pd.notna(value)
            else "資料不足"
        )
    )


# ----------------------------------------
# Candidate table
# ----------------------------------------

if results.empty:
    st.info(
        "目前沒有符合條件的股票，"
        "或個股歷史資料不足。"
    )

else:
    st.success(
        f"本次找到 {len(results)} 檔候選股票。"
    )

    display = results[
        [
            "market",
            "code",
            "name",
            "industry_display",
            "close",
            "score",
            "heat_display",
            "risk_level",
        ]
    ].rename(
        columns={
            "market": "市場",
            "code": "代號",
            "name": "名稱",
            "industry_display": "所屬產業",
            "close": "收盤價",
            "score": "技術分數",
            "heat_display": "產業熱度",
            "risk_level": "追高風險",
        }
    )

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "📥 下載候選股與產業分析 CSV",
        data=results.to_csv(
            index=False
        ).encode("utf-8-sig"),
        file_name=f"screen_v14_{latest}.csv",
        mime="text/csv",
    )

    # ------------------------------------
    # Chase risk overview
    # ------------------------------------

    st.divider()
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
        "此處為追價條件分類，"
        "不代表實際投資虧損機率。"
    )

    # ------------------------------------
    # Individual stock detail
    # ------------------------------------

    st.divider()
    st.subheader("📱 個股詳細分析")

    for _, stock in results.iterrows():
        market_name = (
            "上市"
            if stock["market"] == "TWSE"
            else "上櫃"
        )

        heading = (
            f"{market_name} {stock['code']} "
            f"{stock['name']}｜"
            f"{stock['score']:.1f}分｜"
            f"風險：{stock['risk_level']}"
        )

        with st.expander(heading):
            st.markdown(
                f"### {stock['code']} "
                f"{stock['name']}"
            )

            st.write(
                "**所屬產業：** "
                f"{stock['industry_display']}"
            )

            st.write(
                "**產業熱度：** "
                f"{stock['heat_display']}"
            )

            if pd.isna(stock["industry_heat"]):
                st.info(
                    "該產業的資料仍不足，"
                    "暫不提供熱度百分比。"
                )

            st.divider()

            a, b = st.columns(2)

            a.metric(
                "最新收盤價",
                f"{stock['close']:.2f}",
            )

            b.metric(
                "技術分數",
                f"{stock['score']:.1f}",
            )

            a, b = st.columns(2)

            a.metric(
                "成交量比",
                f"{stock['volume_ratio']:.2f}倍",
            )

            b.metric(
                "20日均線乖離",
                f"{stock['extension_pct']:.2f}%",
            )

            st.markdown("#### 📉 技術參考價")

            a, b = st.columns(2)

            a.metric(
                "20日均線",
                f"{stock['ma20']:.2f}",
            )

            b.metric(
                "60日均線",
                f"{stock['ma60']:.2f}",
            )

            a, b = st.columns(2)

            a.metric(
                "突破參考價",
                f"{stock['breakout_ref']:.2f}",
            )

            b.metric(
                "支撐參考價",
                f"{stock['stop_reference']:.2f}",
            )

            st.metric(
                "收盤至支撐參考距離",
                f"{stock['risk_pct']:.2f}%",
            )

            st.divider()
            st.markdown("#### 🚦 隔日追價風險")

            risk_text = stock["risk_note"]

            if stock["risk_level"] == "高":
                st.error(risk_text)

            elif stock["risk_level"] == "中":
                st.warning(risk_text)

            else:
                st.info(risk_text)

            st.write(
                "**隔日追價警戒參考：** "
                f"{stock['watch_ceiling']:.2f} 元"
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
                "技術參考價與追價警戒價"
                "不是買賣指令；"
                "遇跳空可能無法依參考價成交。"
            )


# ----------------------------------------
# Explanations
# ----------------------------------------

st.divider()

with st.expander("📖 產業熱度如何計算？"):
    st.write(
        "產業熱度為自訂百分制指數，"
        "綜合以下四項因素："
    )

    st.write(
        "成交金額升溫：35分"
    )

    st.write(
        "產業上漲家數比例：30分"
    )

    st.write(
        "相對市場強弱：25分"
    )

    st.write(
        "成交集中度變化：10分"
    )

    st.write(
        "目前需要近期6個交易日的完整資料，"
        "且產業及市場資料覆蓋率"
        "達到設定門檻，才會顯示熱度。"
    )

    st.write(
        "這是初步研究指數，"
        "不是資金淨流入百分比，"
        "也不是隔日上漲機率。"
    )


with st.expander("📚 資料來源及限制"):
    st.write(
        "上市公司及產業分類："
        "TWSE 官方資料。"
    )

    st.write(
        "上櫃公司及產業分類："
        "TPEx 官方資料。"
    )

    st.write(
        "行情由 GitHub Actions 更新，"
        "歷史資料存放於 Supabase。"
    )

    st.write(
        "目前尚未全面完成除權息還原、"
        "基本面、法人籌碼及策略回測。"
    )

    st.write(
        "產業熱度使用現有資料的"
        "等權股票報酬作為市場比較基準，"
        "不是加權股價指數。"
    )


st.caption(
    "台股盤後選股 V1.4｜"
    "產業熱度研究版｜"
    "個人及家庭研究使用"
)
