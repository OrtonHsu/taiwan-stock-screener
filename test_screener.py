import sqlite3
import pandas as pd
from screener import parse_twse, parse_tpex, rank_stocks, connect, store, load

def test_twse_parse():
    r=[{'Code':'2330','Name':'台積電','ClosingPrice':'1,000.00','TradeVolume':'1,000','TradeValue':'1,000,000'}]
    assert parse_twse(r,'2026-10-09')[0][4]==1000

def test_tpex_parse():
    r=[{'SecuritiesCompanyCode':'6488','CompanyName':'測試','Close':'100','TradingShares':'1000','TradingAmount':'100000'}]
    assert parse_tpex(r,'2026-10-09')[0][2]=='6488'

def test_no_history_no_signal():
    d=pd.DataFrame([{'date':'2026-10-09','market':'TWSE','code':'2330','name':'測試','close':100,'volume':1000,'amount':100000}])
    assert rank_stocks(d).empty

def test_db_roundtrip(tmp_path):
    c=connect(tmp_path/'test.sqlite3')
    store(c,[('2026-10-09','TWSE','2330','測試',100.,1000.,100000.)])
    assert len(load(c))==1
