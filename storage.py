"""Persistent PostgreSQL when DATABASE_URL is set; local SQLite otherwise."""
import os
import sqlite3
from pathlib import Path
import pandas as pd

SCHEMA = '''CREATE TABLE IF NOT EXISTS prices (
 date TEXT NOT NULL, market TEXT NOT NULL, code TEXT NOT NULL,
 name TEXT NOT NULL, close DOUBLE PRECISION NOT NULL,
 volume DOUBLE PRECISION NOT NULL, amount DOUBLE PRECISION,
 PRIMARY KEY(date,market,code))'''

class Store:
    def __init__(self, url=None, path='data/quotes.sqlite3'):
        url = url or os.getenv('DATABASE_URL', '')
        self.pg = bool(url)
        if self.pg:
            import psycopg
            self.conn = psycopg.connect(url, connect_timeout=15)
        else:
            path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
            self.conn = sqlite3.connect(path)
        with self.conn.cursor() if self.pg else self.conn as c:
            c.execute(SCHEMA)
        self.conn.commit()
    def store(self, rows):
        if not rows: return 0
        sql = ('INSERT INTO prices(date,market,code,name,close,volume,amount) VALUES '
               + ('(%s,%s,%s,%s,%s,%s,%s)' if self.pg else '(?,?,?,?,?,?,?)')
               + ' ON CONFLICT(date,market,code) DO UPDATE SET name=excluded.name,close=excluded.close,volume=excluded.volume,amount=excluded.amount')
        cur=self.conn.cursor()
        try:
            cur.executemany(sql, rows); self.conn.commit()
        except Exception:
            self.conn.rollback(); raise
        finally: cur.close()
        return len(rows)
    def load(self):
        if self.pg:
            with self.conn.cursor() as cur:
                cur.execute('SELECT date,market,code,name,close,volume,amount FROM prices ORDER BY date,market,code')
                return pd.DataFrame(cur.fetchall(),columns=[x.name for x in cur.description])
        return pd.read_sql_query('SELECT * FROM prices ORDER BY date,market,code',self.conn)
    def close(self): self.conn.close()
