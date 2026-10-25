"""Taiwan stock screener V1.5.3 | dual strategies + convergence + V1.4."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from screener import rank_stocks
from v15_strategy import rank_v15
from ma_convergence import scan_ma_dual
from storage import Store
from industry_heat import get_industries, calculate_industry_heat

st.set_page_config(page_title="台股盤後選股 V1.5.3", page_icon="📈", layout="wide")
st.title("📈 台股盤後選股 V1.5.3")
st.caption("起漲前期｜強勢回檔｜三線蓄勢／突破｜追高風險｜V1.4 強勢突破｜產業熱度｜無自動下單")
st.info("V1.5.3 為盤後研究訊號，尚未經完整歷史回測；分數不是成功率，均線糾結不代表未來一定上漲。")

with st.sidebar:
    st.header("⚙️ 選股設定")
    min_history = st.slider("最低歷史交易日數", 65, 180, 65, 5)
    turnover = st.number_input("20日平均成交金額下限（萬元）", min_value=1000, max_value=100000, value=5000, step=1000)
    vol_ratio = st.slider("V1.4 突破模式量比門檻", 1.0, 4.0, 1.5, 0.1)
    st.caption("起漲與回檔模式的量能條件由 V1.5 引擎獨立判斷；成交金額與歷史日數設定共用。")
    st.info("GitHub Actions 更新行情至 Supabase。本網站只讀取資料，不寫入或自動下單。")

@st.cache_data(ttl=3600, show_spinner=False)
def load_industry_profiles():
    return get_industries()

try:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        try:
            database_url = st.secrets.get("DATABASE_URL", "")
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
for col in ("date", "market", "code"):
    prices[col] = prices[col].astype(str)

# Keep original data untouched; exclude legacy 'TPEx' records for ranking.
recognized = prices[prices["market"].isin(["TWSE", "TPEX"])].copy()
if recognized.empty:
    st.error("找不到 TWSE 或 TPEX 行情，請先確認市場標記。")
    st.stop()

latest = recognized["date"].max()
today = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
latest_prices = recognized[recognized["date"] == latest]
latest_count = len(latest_prices[["market", "code"]].drop_duplicates())
market_counts = latest_prices.groupby("market")["code"].nunique().to_dict()
trading_days = recognized["date"].nunique()

profiles = pd.DataFrame()
heat = pd.DataFrame()
industry_error = None
with st.spinner("讀取官方產業分類並計算熱度..."):
    try:
        profiles = load_industry_profiles()
        if not profiles.empty:
            heat = calculate_industry_heat(recognized, profiles)
    except Exception as exc:
        industry_error = str(exc)

st.subheader("📊 最新行情概況")
a, b, c = st.columns(3)
a.metric("最新資料日期", latest)
b.metric("累積交易日期", f"{trading_days:,}")
c.metric("最新行情股票", f"{latest_count:,}")
a, b = st.columns(2)
a.metric("上市 TWSE", f"{market_counts.get('TWSE', 0):,}")
b.metric("上櫃 TPEX", f"{market_counts.get('TPEX', 0):,}")
other_count = len(prices[prices["date"] == latest][["market", "code"]].drop_duplicates()) - latest_count
if other_count > 0:
    st.warning(f"最新日期另有 {other_count} 筆非 TWSE/TPEX 市場標記；已從 V1.5 排名排除。")
if latest != today:
    st.info(f"行情日期為 {latest}；可能因休市或尚未更新，請依交易所交易日確認。")

st.divider()
st.subheader("🗂️ 歷史資料完整度")
history_counts = recognized.groupby(["market", "code"])["date"].nunique().reset_index(name="history_days")
coverage = latest_prices[["market", "code"]].drop_duplicates().merge(history_counts, on=["market", "code"], how="left")
qualified = int((coverage["history_days"] >= min_history).sum())
coverage_pct = qualified / latest_count * 100 if latest_count else 0
st.progress(min(1.0, coverage_pct / 100))
st.write(f"已有 **{qualified:,} / {latest_count:,}** 檔累積至少 **{min_history}** 個行情交易日（{coverage_pct:.1f}%）。")
st.caption("累積天數不保證最近六日完整，也尚未進行全面除權息還原。")

st.divider()
st.subheader("🔥 產業熱度指數")
st.caption("0～100 分的自訂活躍度與相對強弱指標，不是資金淨流入百分比或隔日上漲機率。")
if industry_error:
    st.warning(f"產業資料暫不可用：{industry_error}")
if profiles.empty:
    st.info("官方產業分類未取得；個股選股仍可使用。")
else:
    valid_heat = heat[heat["status"] == "可計算"].copy() if not heat.empty else pd.DataFrame()
    h1, h2 = st.columns(2)
    h1.metric("官方產業種類", profiles["industry"].nunique())
    h2.metric("已有熱度分數產業", len(valid_heat))
    if valid_heat.empty:
        st.info("最近六日行情或產業／全市場覆蓋率不足，暫無可計算分數。")
    else:
        heat_display = valid_heat[["industry", "industry_heat", "coverage_pct"]].rename(columns={"industry": "產業", "industry_heat": "熱度分數（0～100）", "coverage_pct": "資料覆蓋率（%）"})
        st.dataframe(heat_display, use_container_width=True, hide_index=True)


def attach_industry(frame):
    """Enrich a candidate frame, including when heat data is unavailable."""
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    if not profiles.empty:
        meta = profiles[["market", "code", "industry"]].drop_duplicates(["market", "code"])
        result = result.merge(meta, on=["market", "code"], how="left")
    else:
        result["industry"] = None
    if not heat.empty and {"industry", "industry_heat"}.issubset(heat.columns):
        result = result.merge(heat[["industry", "industry_heat"]].drop_duplicates("industry"), on="industry", how="left")
    else:
        result["industry_heat"] = None
    result["industry_display"] = result["industry"].fillna("未分類")
    result["heat_display"] = result["industry_heat"].apply(lambda v: f"{float(v):.1f} 分" if pd.notna(v) else "資料不足")
    return result

with st.spinner("計算 V1.5 與 V1.4 選股訊號..."):
    try:
        dual_all = rank_v15(recognized, min_history=min_history, min_amount=turnover * 10000)
        classic_all = rank_stocks(recognized, min_history=min_history, min_amount=turnover * 10000, vol_ratio=vol_ratio)
    except Exception as exc:
        st.error(f"選股計算失敗：{exc}")
        st.stop()

if not dual_all.empty:
    dual_all = dual_all[dual_all["date"].astype(str) == latest].copy()
if not classic_all.empty:
    classic_all = classic_all[classic_all["date"].astype(str) == latest].copy()

dual = attach_industry(dual_all)
classic = attach_industry(classic_all)

st.divider()
st.subheader("🎯 V1.5.3 多模式選股")
st.caption("模式分數是啟發式排序；均線糾結與多頭轉強標籤並非買入建議。")

tab_a, tab_b, tab_ma, tab_risk, tab_old = st.tabs([
    "🌱 起漲前期", "🎯 強勢回檔", "🧲 三線糾結", "🚦 追高警示", "🏆 V1.4 突破榜"
])


def show_v15_table(frame, key):
    if frame.empty:
        st.info("最新交易日沒有符合目前條件的股票。可調整成交金額門檻，但不建議為了產生名單而放寬風險規則。")
        return
    shown = frame.head(20).copy()
    cols = ["market", "code", "name", "industry_display", "close", "score", "extension_pct", "change5_pct", "volume_ratio", "support_reference", "risk_pct", "risk_level", "heat_display"]
    labels = {"market": "市場", "code": "代號", "name": "名稱", "industry_display": "產業", "close": "收盤價", "score": "模式分數", "extension_pct": "MA20乖離%", "change5_pct": "五日漲幅%", "volume_ratio": "成交量比", "support_reference": "支撐參考", "risk_pct": "至支撐距離%", "risk_level": "追高風險", "heat_display": "產業熱度"}
    st.dataframe(shown[cols].rename(columns=labels), use_container_width=True, hide_index=True)
    st.download_button("📥 下載完整候選名單 CSV", data=frame.to_csv(index=False).encode("utf-8-sig"), file_name=f"v15_{key}_{latest}.csv", mime="text/csv", key=f"download_{key}")
    st.caption(f"符合條件共 {len(frame)} 筆；表格最多顯示 20 筆。")
    for _, stock in shown.head(10).iterrows():
        title = f"{stock['code']} {stock['name']}｜{stock['score']:.1f}分｜風險：{stock['risk_level']}"
        with st.expander(title):
            st.write(f"**模式：** {stock['mode']}｜**訊號：** {stock['signal']}")
            st.write(f"**產業：** {stock['industry_display']}｜**產業熱度：** {stock['heat_display']}")
            a, b = st.columns(2)
            a.metric("最新收盤", f"{stock['close']:.2f}")
            b.metric("MA20 乖離", f"{stock['extension_pct']:+.2f}%")
            a, b = st.columns(2)
            a.metric("MA20", f"{stock['ma20']:.2f}")
            b.metric("MA60", f"{stock['ma60']:.2f}")
            a, b = st.columns(2)
            a.metric("支撐參考", f"{stock['support_reference']:.2f}")
            b.metric("至支撐距離", f"{stock['risk_pct']:.2f}%")
            st.write(f"**風險解讀：** {stock['risk_note']}")
            st.caption("支撐採歷史收盤價與均線估算，尚不能辨識盤中低點、K棒止跌或確定停損成交價。")

with tab_a:
    early = dual[dual["mode"] == "A 起漲前期"].copy() if not dual.empty else pd.DataFrame()
    st.write("找剛轉強、尚未明顯遠離 20 日均線的股票；需留意假突破。")
    show_v15_table(early, "early")

with tab_b:
    pullback = dual[dual["mode"] == "B 強勢回檔"].copy() if not dual.empty else pd.DataFrame()
    st.write("找既有上升趨勢、股價回到均線附近的股票；目前僅能判斷收盤回測，仍須確認止跌。")
    show_v15_table(pullback, "pullback")

with tab_ma:
    st.write("根據 5／10／20 日均線，分別篩選尚未突破的蓄勢觀察股，以及近期糾結後收盤放量突破的股票。")
    threshold = st.select_slider(
        "三線糾結門檻", options=[1.0, 2.0, 3.0], value=2.0,
        format_func=lambda v: f"≤ {v:g}%", key="dual_ma_threshold",
    )
    breakout_vol = st.slider("突破確認量比門檻", 1.0, 3.0, 1.5, 0.1, key="dual_ma_vol")
    st.caption("突破必須在過去 5 個交易日內曾有三線糾結，並以最新收盤價突破前 10 日最高收盤價；均線離開糾結區仍可入選。")
    with st.spinner("計算三線蓄勢與放量突破訊號..."):
        try:
            ma_all = scan_ma_dual(
                recognized,
                threshold_pct=threshold,
                max_extension_pct=3.0,
                min_amount=turnover * 10000,
                min_history=min_history,
                breakout_volume_ratio=breakout_vol,
                lookback_days=5,
            )
            ma_all = attach_industry(ma_all)
        except Exception as exc:
            st.error(f"三線雙模式計算失敗：{exc}")
            ma_all = pd.DataFrame()

    if ma_all.empty:
        st.info("最新交易日沒有符合雙模式門檻的股票。近期上櫃行情缺漏也可能降低符合資格的股票數量。")
    else:
        waiting = ma_all[ma_all["mode"] == "A 蓄勢觀察"].copy()
        breaking = ma_all[ma_all["mode"] == "B 突破確認"].copy()
        x, y, z = st.columns(3)
        x.metric("蓄勢觀察", len(waiting))
        y.metric("突破確認", len(breaking))
        z.metric("嚴格糾結蓄勢 ≤1%", int((waiting["convergence_pct"] <= 1).sum()))

        tab_watch, tab_break = st.tabs(["🌱 蓄勢觀察", "🚀 突破確認"])

        def show_ma_mode_table(frame, mode_key):
            if frame.empty:
                st.info("最新交易日沒有符合這個模式的股票。")
                return
            labels = {
                "market": "市場", "code": "代號", "name": "名稱", "industry_display": "產業",
                "close": "收盤價", "ma5": "MA5", "ma10": "MA10", "ma20": "MA20",
                "convergence_pct": "目前三線糾結率%", "extension_pct": "MA20乖離%",
                "ma20_slope_pct": "MA20近5期變化%", "volume_ratio": "成交量比",
                "status": "型態狀態", "tightness": "糾結程度", "heat_display": "產業熱度",
                "convergence_day": "最近糾結日期", "convergence_days_ago": "距糾結交易日數",
                "breakout_reference": "前10日最高收盤", "breakout_pct": "突破幅度%",
            }
            columns = [
                "market", "code", "name", "industry_display", "close",
                "ma5", "ma10", "ma20", "convergence_pct", "extension_pct",
                "ma20_slope_pct", "volume_ratio", "status", "tightness",
                "heat_display",
            ]
            if mode_key == "breakout":
                columns += ["convergence_day", "convergence_days_ago", "breakout_reference", "breakout_pct"]
            st.dataframe(frame.head(50)[columns].rename(columns=labels), use_container_width=True, hide_index=True)
            st.caption(f"符合條件共 {len(frame)} 檔；表格最多顯示 50 檔。突破量比使用前 20 個交易日平均量。")
            st.download_button(
                "📥 下載此模式完整 CSV",
                data=frame.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"ma_v153_{mode_key}_{latest}.csv", mime="text/csv",
                key=f"download_ma_{mode_key}",
            )
            for _, stock in frame.head(10).iterrows():
                with st.expander(f"{stock['code']} {stock['name']}｜糾結率 {stock['convergence_pct']:.3f}%｜{stock['status']}"):
                    st.write(f"**訊號：** {stock['signal']}")
                    st.write(f"**產業：** {stock['industry_display']}｜**產業熱度：** {stock['heat_display']}")
                    u, v, w = st.columns(3)
                    u.metric("MA5", f"{stock['ma5']:.2f}")
                    v.metric("MA10", f"{stock['ma10']:.2f}")
                    w.metric("MA20", f"{stock['ma20']:.2f}")
                    u, v = st.columns(2)
                    u.metric("收盤價", f"{stock['close']:.2f}")
                    v.metric("成交量比", f"{stock['volume_ratio']:.2f} 倍")
                    if mode_key == "breakout":
                        st.write(f"**最近糾結日期：** {stock['convergence_day']}｜**突破參考價：** {stock['breakout_reference']:.2f}｜**突破幅度：** {stock['breakout_pct']:+.2f}%")
                    st.write(f"**20日平均成交金額：** {stock['avg_turnover']/10000:,.0f} 萬元｜**歷史行情：** {stock['history_days']} 日")
                    st.caption("僅根據盤後收盤價判斷，不代表隔日股價表現或實際可成交價格。")

        with tab_watch:
            st.write("仍在均線收斂區、MA20 未明顯下彎，且尚未符合突破確認的股票。")
            show_ma_mode_table(waiting, "watch")
        with tab_break:
            st.write("近期曾糾結，今日收盤向上突破前 10 日收盤高點且成交量放大。")
            show_ma_mode_table(breaking, "breakout")

with tab_risk:
    risky = dual[dual["risk_level"] == "高"].copy() if not dual.empty else pd.DataFrame()
    st.write("本頁顯示 V1.5 雙模式候選中追高風險較高的股票，不代表全市場所有高風險股。")
    if risky.empty:
        st.info("目前沒有高追價風險的 V1.5 候選股。")
    else:
        show_v15_table(risky, "high_risk")
    if not dual.empty:
        counts = dual["risk_level"].value_counts()
        a, b, c = st.columns(3)
        a.metric("低風險標籤", int(counts.get("低", 0)))
        b.metric("中風險標籤", int(counts.get("中", 0)))
        c.metric("高風險標籤", int(counts.get("高", 0)))
        st.caption("標籤描述追價條件，而不是實際虧損機率。")

with tab_old:
    st.write("保留 V1.4：突破前 20 日高點、量能放大、20/60 日均線多頭的傳統強勢榜。")
    if classic.empty:
        st.info("最新交易日沒有符合 V1.4 條件的股票。")
    else:
        old = classic.sort_values(["score", "risk_pct"], ascending=[False, True]).head(10)
        old_cols = ["market", "code", "name", "industry_display", "close", "score", "heat_display", "risk_level"]
        st.dataframe(old[old_cols].rename(columns={"market": "市場", "code": "代號", "name": "名稱", "industry_display": "所屬產業", "close": "收盤價", "score": "技術分數", "heat_display": "產業熱度", "risk_level": "追高風險"}), use_container_width=True, hide_index=True)
        st.download_button("📥 下載 V1.4 候選股 CSV", data=old.to_csv(index=False).encode("utf-8-sig"), file_name=f"screen_v14_{latest}.csv", mime="text/csv", key="download_v14")
        for _, stock in old.iterrows():
            with st.expander(f"{stock['code']} {stock['name']}｜{stock['score']:.1f}分｜風險：{stock['risk_level']}"):
                st.write(f"**產業：** {stock['industry_display']}｜**熱度：** {stock['heat_display']}")
                a, b = st.columns(2)
                a.metric("收盤價", f"{stock['close']:.2f}")
                b.metric("成交量比", f"{stock['volume_ratio']:.2f}倍")
                a, b = st.columns(2)
                a.metric("MA20", f"{stock['ma20']:.2f}")
                b.metric("MA60", f"{stock['ma60']:.2f}")
                a, b = st.columns(2)
                a.metric("突破參考價", f"{stock['breakout_ref']:.2f}")
                b.metric("支撐參考價", f"{stock['stop_reference']:.2f}")
                st.write(f"**MA20 乖離：** {stock['extension_pct']:.2f}%｜**至支撐距離：** {stock['risk_pct']:.2f}%")
                st.write(f"**隔日追價警戒參考：** {stock['watch_ceiling']:.2f} 元")
                st.write(f"**近20日平均成交金額：** {stock['avg_turnover']/10000:,.0f} 萬元｜**歷史資料：** {stock['history_days']} 日")
                st.write(f"**風險提示：** {stock['risk_note']}")
                st.caption("隔日參考價不代表委託價格；跳空、滑價與流動性可能影響成交。")

st.divider()
with st.expander("📖 產業熱度與 V1.5.3 模式說明"):
    st.write("產業熱度：成交金額升溫35分、上漲家數30分、相對市場強弱25分、成交集中度10分；全市場與產業均須達到設定覆蓋率。")
    st.write("A 起漲前期：接近均線、量能漸強但尚未過度延伸。B 強勢回檔：已形成上升趨勢，收盤價接近 MA20。")
    st.write("三線糾結：MA5、MA10、MA20 最高與最低的差距除以三線均值；門檻 1%、2% 或 3%，且股價距 MA20 不超過 ±3%。")
    st.write("追價風險屬啟發式分類，並未進行成功率校準；V1.5 分數不可當成隔日上漲機率。")
with st.expander("📚 資料來源及限制"):
    st.write("行情來自 TWSE、TPEx 官方公開資料，透過 GitHub Actions 更新至 Supabase。")
    st.write("產業分類來自 TWSE、TPEx 官方公司資料，可能因 API 更新或短暫斷線而無法讀取。")
    st.write("目前未全面進行除權息還原、OHLC K棒分析、基本面／法人籌碼研究及完整交易成本回測。")

st.caption("台股盤後選股 V1.5.3｜多模式研究版｜個人及家庭研究使用")
