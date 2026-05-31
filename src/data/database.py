"""
本地 SQLite 数据库管理 (三源合一版)
数据源: baostock(指数/个股K线) + tushare(融资融券/北向/国债) + akshare(AH溢价)
- 初始化表结构
- 增量数据写入 (INSERT OR REPLACE)
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

DB_PATH = os.environ.get(
    "HEAT_INDEX_DB",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "heat_index.db")
)

# ── 建表 SQL ──────────────────────────────────────────────────────────────────
SCHEMA = """
-- 指数日行情 (baostock: query_history_k_data_plus)
CREATE TABLE IF NOT EXISTS index_daily (
    trade_date TEXT NOT NULL,
    index_code TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL, pct_change REAL,
    PRIMARY KEY (trade_date, index_code)
);

-- 个股日行情 (baostock: query_history_k_data_plus)
-- 列名对齐 baostock 返回字段: peTTM, pbMRQ, pctChg
CREATE TABLE IF NOT EXISTS stock_daily (
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    pct_change REAL,
    peTTM REAL,             -- PE-TTM (baostock 字段名)
    pbMRQ REAL,             -- PB-MRQ 最新季报 (baostock 字段名)
    total_mv REAL,          -- 总市值(元)
    circ_mv REAL,           -- 流通市值(元)
    PRIMARY KEY (trade_date, stock_code)
);

-- 个股行业分类 (baostock: query_stock_industry)
CREATE TABLE IF NOT EXISTS stock_industry (
    code TEXT NOT NULL,           -- akshare格式 sh.600000
    code_name TEXT,               -- 股票名称
    industry TEXT,                -- 行业名称
    industry_classification TEXT, -- 证监会行业分类
    update_date TEXT,             -- 更新日期
    PRIMARY KEY (code)
);

-- M2月度货币供应量 (akshare: macro_china_money_supply)
CREATE TABLE IF NOT EXISTS m2_monthly (
    month       TEXT PRIMARY KEY,
    m2_billion  REAL,
    m2_yoy      REAL
);

-- A股总市值 (stock_daily total_mv 成分股加总proxy)
CREATE TABLE IF NOT EXISTS stock_market_cap (
    trade_date  TEXT PRIMARY KEY,
    total_mv    REAL,
    stock_count INTEGER
);

-- 个股资产负债表 (baostock: query_balance_data)
CREATE TABLE IF NOT EXISTS stock_balance (
    stock_code TEXT NOT NULL,
    report_date TEXT NOT NULL,    -- 报告期 YYYY-MM-DD
    bps REAL,                     -- 每股净资产
    PRIMARY KEY (stock_code, report_date)
);

-- 融资融券 (tushare: margin 接口, 沪深北三市合并日汇总)
CREATE TABLE IF NOT EXISTS margin_history (
    trade_date TEXT NOT NULL PRIMARY KEY,
    rzye REAL,       -- 融资余额(元)
    rzmre REAL,      -- 融资买入额(元)
    rzche REAL,      -- 融资偿还额(元)
    rqye REAL,       -- 融券余额(元)
    rqmcl REAL,      -- 融券卖出量(股)
    rzrqye REAL      -- 融资融券余额(元)
);

-- 北向资金 (tushare: moneyflow_hsgt 接口)
CREATE TABLE IF NOT EXISTS northbound_history (
    trade_date TEXT NOT NULL PRIMARY KEY,
    hgt REAL,           -- 沪股通当日成交额(百万元)
    sgt REAL,           -- 深股通当日成交额(百万元)
    north_net REAL,     -- 北向净流入(百万元)
    south_money REAL    -- 南向资金(百万元)
);

-- 国债收益率 (tushare: yc_cb 中债国债收益率曲线)
CREATE TABLE IF NOT EXISTS bond_yield (
    trade_date TEXT NOT NULL,
    curve_term REAL NOT NULL,     -- 期限(年): 0.08,0.25,...,10,30,50
    yield_rate REAL,              -- 收益率(%)
    PRIMARY KEY (trade_date, curve_term)
);

-- 指数PE/PB历史 (tushare: index_dailybasic 接口, 含换手率)
CREATE TABLE IF NOT EXISTS index_pe_history (
    trade_date TEXT NOT NULL,
    index_code TEXT NOT NULL,
    pe_ttm REAL,                 -- PE-TTM
    pb REAL,                     -- PB
    total_mv REAL,               -- 总市值(亿元)
    turnover_rate REAL,          -- 换手率(%)
    PRIMARY KEY (trade_date, index_code)
);

-- 涨停明细 (由 stock_daily.pct_change >= 9.9 筛选写入)
CREATE TABLE IF NOT EXISTS limit_up_daily (
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    PRIMARY KEY (trade_date, stock_code)
);

-- AH 溢价指数 (akshare: stock_zh_ah_spot_em)
CREATE TABLE IF NOT EXISTS ah_premium (
    trade_date TEXT NOT NULL PRIMARY KEY,
    premium REAL                 -- 溢价率(%)
);

-- 新增投资者 (中国结算, 手动录入)
CREATE TABLE IF NOT EXISTS new_investors (
    week_end_date TEXT NOT NULL PRIMARY KEY,
    new_accounts REAL            -- 新增户数(万户)
);

-- 热度指数计算结果
CREATE TABLE IF NOT EXISTS heat_index (
    trade_date TEXT NOT NULL PRIMARY KEY,
    composite_score REAL NOT NULL,  -- 综合热度 0-100
    dim_valuation REAL,             -- 估值维度
    dim_fund REAL,                  -- 资金维度
    dim_sentiment REAL,             -- 情绪维度
    dim_technical REAL,             -- 技术维度
    dim_structure REAL,             -- 结构维度
    detail_json TEXT,               -- 所有子指标详情
    created_at TEXT DEFAULT (datetime('now'))
);

-- 板块热度 (Phase 2: 行业指数)
CREATE TABLE IF NOT EXISTS sector_heat (
    trade_date TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    composite_score REAL NOT NULL,
    detail_json TEXT,
    PRIMARY KEY (trade_date, sector_code)
);

-- 元数据
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


def save_dataframe(df: pd.DataFrame, table: str, db_path: str = None):
    """保存 DataFrame 到数据库（INSERT OR REPLACE upsert）"""
    if df.empty:
        return
    with get_conn(db_path) as conn:
        df.to_sql('_tmp_upsert', conn, if_exists='replace', index=False)
        cols = ', '.join(df.columns)
        conn.execute(f'INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM _tmp_upsert')
        conn.execute('DROP TABLE _tmp_upsert')
    logger.info('Saved %d rows to %s', len(df), table)


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
