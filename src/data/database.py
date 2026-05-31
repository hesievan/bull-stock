"""
本地 SQLite 数据库管理
- 初始化表结构
- 增量数据写入
- 查询接口
"""
import sqlite3
import os
import logging
from datetime import datetime, date
from typing import Optional
from contextlib import contextmanager

import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("HEAT_INDEX_DB", os.path.join(os.path.dirname(__file__), "..", "..", "data", "heat_index.db"))

# 建表 SQL
SCHEMA = """
-- 指数日行情
CREATE TABLE IF NOT EXISTS index_daily (
    trade_date TEXT NOT NULL,
    index_code TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    amount REAL,
    pct_change REAL,
    PRIMARY KEY (trade_date, index_code)
);

-- 个股日行情（精简字段）
CREATE TABLE IF NOT EXISTS stock_daily (
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    amount REAL,
    pct_change REAL,
    pe REAL,
    pb REAL,
    total_mv REAL,       -- 总市值(万)
    circ_mv REAL,        -- 流通市值(万)
    PRIMARY KEY (trade_date, stock_code)
);

-- 个股资产负债表（用于破净率计算）
CREATE TABLE IF NOT EXISTS stock_balance (
    stock_code TEXT NOT NULL,
    report_date TEXT NOT NULL,
    bps REAL,            -- 每股净资产
    PRIMARY KEY (stock_code, report_date)
);

-- 融资融券
CREATE TABLE IF NOT EXISTS margin_daily (
    trade_date TEXT NOT NULL,
    stock_code TEXT,
    margin_balance REAL,     -- 融资余额(元)
    margin_buy REAL,         -- 融资买入额(元)
    PRIMARY KEY (trade_date, stock_code)
);

-- 北向资金
CREATE TABLE IF NOT EXISTS northbound_daily (
    trade_date TEXT NOT NULL,
   净流入 REAL,             -- 亿元
    southbound REAL,        -- 南下(亿元)
    PRIMARY KEY (trade_date)
);

-- 债券收益率
CREATE TABLE IF NOT EXISTS bond_yield (
    trade_date TEXT NOT NULL,
    yield_1y REAL,
    yield_10y REAL,
    PRIMARY KEY (trade_date)
);

-- 涨停数据
CREATE TABLE IF NOT EXISTS limit_up_daily (
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    PRIMARY KEY (trade_date, stock_code)
);

-- AH 溢价指数
CREATE TABLE IF NOT EXISTS ah_premium (
    trade_date TEXT NOT NULL,
    premium REAL,           -- 溢价率百分比
    PRIMARY KEY (trade_date)
);

-- 新增投资者数据
CREATE TABLE IF NOT EXISTS new_investors (
    week_end_date TEXT NOT NULL,
    new_accounts REAL,      -- 万户
    PRIMARY KEY (week_end_date)
);

-- 计算结果
CREATE TABLE IF NOT EXISTS heat_index (
    trade_date TEXT NOT NULL PRIMARY KEY,
    composite_score REAL NOT NULL,
    dimension_valuation REAL,
    dimension_fund REAL,
    dimension_sentiment REAL,
    dimension_technical REAL,
    dimension_structure REAL,
    detail_json TEXT,       -- JSON: 所有子指标数值
    created_at TEXT DEFAULT (datetime('now'))
);

-- 板块热度
CREATE TABLE IF NOT EXISTS sector_heat (
    trade_date TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    composite_score REAL NOT NULL,
    detail_json TEXT,
    PRIMARY KEY (trade_date, sector_code)
);

-- 元数据记录
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


@contextmanager
def get_conn(db_path: str = None):
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database(db_path: str = None):
    """初始化数据库表结构"""
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
    logger.info("Database initialized at %s", db_path or DB_PATH)


def save_dataframe(df: pd.DataFrame, table: str, if_exists: str = "append", db_path: str = None):
    """保存 DataFrame 到数据库"""
    if df.empty:
        return
    with get_conn(db_path) as conn:
        df.to_sql(table, conn, if_exists=if_exists, index=False)
    logger.info("Saved %d rows to %s", len(df), table)


def read_dataframe(query: str, params=None, db_path: str = None) -> pd.DataFrame:
    """从数据库读取 DataFrame"""
    with get_conn(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_latest_date(table: str, date_col: str = "trade_date", db_path: str = None) -> Optional[str]:
    """获取最新日期"""
    with get_conn(db_path) as conn:
        row = conn.execute(
            f"SELECT MAX({date_col}) as d FROM {table}"
        ).fetchone()
    return row["d"] if row and row["d"] else None


def record_meta(key: str, value: str, db_path: str = None):
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key,value,updated_at) VALUES(?,?,datetime('now'))",
            (key, value)
        )


def get_meta(key: str, db_path: str = None) -> Optional[str]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    init_database(path)
    print(f"Database initialized at {path}")
