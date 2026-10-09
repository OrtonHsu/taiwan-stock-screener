import streamlit as st
from screener import connect, update, load, rank_stocks, import_csv

st.set_page_config(page_title='台股盤後選股 V1.0', page_icon='📈', layout='wide')
st.title('📈 台股盤後選股 V1.0')
st.caption('TWSE + TPEx 官方每日行情｜研究用途｜不提供自動下單｜不保證隔日上漲')
st.warning('資料日期必須核對。官方最新快照可能在休市或資料延遲時仍是上一交易日；本版尚未自動驗證官方交易日。')
conn=connect()
with st.sidebar:
    st.header('選股設定')
    min_history=st.slider('最低歷史交易日數',65,180,65)
    turnover=st.number_input('20日平均成交金額下限（萬元）',min_value=1000,max_value=100000,value=5000,step=1000)
    vol_ratio=st.slider('當日量／20日均量',1.0,4.0,1.5,0.1)
    st.divider()
    st.write('資料維護')
    confirm=st.checkbox('已核對今日為交易日，且官方最新行情屬於今日')
    if st.button('手動更新官方快照',disabled=not confirm):
        with st.spinner('下載官方行情中…'):
            count, errors=update(conn)
        if count: st.success(f'寫入 {count} 筆快照；請確認日期與價格')
        for err in errors: st.error(err)
    upload=st.file_uploader('匯入經核對的歷史 CSV',type='csv')
    if upload and st.button('匯入 CSV'):
        try: st.success(f'匯入 {import_csv(conn,upload.getvalue())} 筆')
        except Exception as e: st.error(str(e))

prices=load(conn)
if prices.empty:
    st.info('資料庫目前沒有行情。請匯入歷史 CSV，或於交易日16:00後核對官方行情再更新。')
    st.stop()
latest=prices['date'].max()
cols=st.columns(3)
cols[0].metric('資料最新日期',latest)
cols[1].metric('累積交易日期數',prices['date'].nunique())
cols[2].metric('最新快照股票數',len(prices[prices.date==latest]))
if prices['date'].nunique()<min_history:
    st.warning(f'歷史日期只有 {prices.date.nunique()} 天；至少需要 {min_history} 個交易日才可篩選。不可把不足的資料當作有效訊號。')
results=rank_stocks(prices,min_history,turnover*10000,vol_ratio)
# The ranking is only for stocks with data on the latest date.
if not results.empty: results=results[results.date==latest]
st.subheader('最新交易日候選股')
if results.empty: st.info('沒有符合條件的股票，或歷史資料仍不足。')
else:
    st.dataframe(results.head(10),use_container_width=True,hide_index=True)
    st.download_button('下載完整候選股 CSV',results.to_csv(index=False).encode('utf-8-sig'),file_name=f'screen_{latest}.csv',mime='text/csv')
with st.expander('資料來源、計分及限制'):
    st.write('上市：TWSE STOCK_DAY_ALL；上櫃：TPEx mainboard_daily_close_quotes。')
    st.write('條件：收盤價 > MA20 > MA60、突破前20日最高收盤價、成交量放大、MA60正斜率、20日均成交金額達標。')
    st.write('分數為啟發式技術面排序，並非上漲機率；本版尚未串接三大法人與財報，也沒有策略回測。')
    st.write('請注意除權息、處置股、下市股票、休市、資料格式變更及歷史資料不完整。')
