import os
import streamlit as st
from screener import rank_stocks, import_csv
from storage import Store

st.set_page_config(page_title='台股盤後選股 V1.1',page_icon='📈',layout='wide')
st.title('📈 台股盤後選股 V1.1')
st.caption('官方可核對歷史資料｜無自動下單｜不保證隔日上漲')
st.warning('本版自動歷史回補僅涵蓋設定的上市觀察清單；上櫃與全市場歷史回補尚未完成，不能視為全市場前10名。')
try:
    url=os.getenv('DATABASE_URL','')
    if not url:
        try: url=st.secrets.get('DATABASE_URL','')
        except Exception: pass
    db=Store(url=url)
except Exception as e:
    st.error(f'資料庫連線失敗：{e}'); st.stop()
with st.sidebar:
    st.header('選股設定')
    min_history=st.slider('最低歷史交易日數',65,180,65)
    turnover=st.number_input('20日平均成交金額下限（萬元）',1000,100000,5000,1000)
    vol_ratio=st.slider('當日量／20日均量',1.0,4.0,1.5,0.1)
    st.divider()
    st.write('資料維護')
    st.info('每日更新由 GitHub Actions 執行；本頁不再提供無法核對日期的快照更新。')
    upload=st.file_uploader('匯入經核對的歷史 CSV',type='csv')
    if upload and st.button('匯入 CSV'):
        try:
            from screener import import_csv
            import io,pandas as pd
            frame=pd.read_csv(io.BytesIO(upload.getvalue()),dtype={'code':str,'date':str,'market':str})
            required=['date','market','code','name','close','volume','amount']
            if not set(required).issubset(frame.columns): raise ValueError('CSV 欄位不完整')
            if not frame.date.astype(str).str.fullmatch(r'\d{4}-\d{2}-\d{2}').all(): raise ValueError('日期格式錯誤')
            if not frame.market.isin(['TWSE','TPEX']).all(): raise ValueError('market 不合法')
            if not frame.code.str.fullmatch(r'\d{4}').all(): raise ValueError('股票代號不合法')
            rows=[]
            for d,m,c,n,p,v,a in frame[required].itertuples(index=False,name=None):
                if pd.notna(p) and pd.notna(v) and pd.notna(a) and float(p)>0 and float(v)>0 and float(a)>=0:
                    rows.append((d,m,c,n,float(p),float(v),float(a)))
            st.success(f'匯入 {db.store(rows)} 筆')
        except Exception as e: st.error(str(e))
prices=db.load()
if prices.empty:
    st.info('資料庫尚無行情。請先在 GitHub Actions 手動執行「Update TWSE History」初始化。')
    st.stop()
latest=prices.date.max()
cols=st.columns(3)
cols[0].metric('資料最新日期',latest)
cols[1].metric('累積交易日期數',prices.date.nunique())
cols[2].metric('最新資料股票數',len(prices[prices.date==latest]))
if prices.date.nunique()<min_history: st.warning(f'資料庫只有 {prices.date.nunique()} 個交易日，至少需要 {min_history} 日。')
results=rank_stocks(prices,min_history,turnover*10000,vol_ratio)
if not results.empty: results=results[results.date==latest]
st.subheader('最新交易日候選股（觀察清單範圍）')
if results.empty: st.info('觀察清單中目前沒有符合條件的股票，或個股歷史不足。')
else:
    st.dataframe(results.head(10),use_container_width=True,hide_index=True)
    st.download_button('下載候選股 CSV',results.to_csv(index=False).encode('utf-8-sig'),file_name=f'screen_{latest}.csv',mime='text/csv')
with st.expander('資料來源與限制'):
    st.write('上市歷史：TWSE STOCK_DAY 單股單月官方查詢；上櫃歷史尚需另行驗證。')
    st.write('本版不是全市場掃描，分數不是上漲機率；未處理除權息還原、法人、處置股與回測。')
