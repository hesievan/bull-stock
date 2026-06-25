"""
本地 SQLite 数据库管理 (三源合一版)
数据源: tushare(全市场/融资融券/北向) + akshare(M2/AH溢价)
- 初始化表结构
- 增量数据写入 (INSERT OR REPLACE)
- 查询接口
"""
import json
import sqlite3
import os
import logging
from datetime import date
from typing import Optional
from contextlib import contextmanager

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get(
    "HEAT_INDEX_DB",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "heat_index.db")
)

# ── 建表 SQL ──────────────────────────────────────────────────────────────────
SCHEMA_VERSION = 5

SCHEMA = """
-- 指数日行情 (tushare index_daily)
CREATE TABLE IF NOT EXISTS index_daily (
    trade_date TEXT NOT NULL,
    index_code TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL, pct_change REAL,
    PRIMARY KEY (trade_date, index_code)
);

-- 个股日行情 (tushare daily + daily_basic)
-- 列名: peTTM, pbMRQ, pctChg
CREATE TABLE IF NOT EXISTS stock_daily (
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    pct_change REAL,
    peTTM REAL,             -- PE-TTM
    pbMRQ REAL,             -- PB-MRQ 最新季报
    total_mv REAL,          -- 总市值(万元, tushare)
    circ_mv REAL,           -- 流通市值(万元, tushare)
    turnover_rate REAL,     -- 换手率(%, tushare daily_basic)
    PRIMARY KEY (trade_date, stock_code)
);

-- 个股行业分类 (tushare stock_basic)
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

-- 个股资产负债表
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
    premium REAL,                -- 溢价率(%)
    n_stocks INTEGER,            -- 有效股对数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    composite_score_smoothed REAL,  -- 平滑后综合热度
    heat_level TEXT,                -- 热度等级 green/yellow/orange/red
    heat_level_smoothed TEXT,       -- 平滑后等级
    dim_valuation REAL,             -- 估值维度
    dim_macro REAL,                 -- 宏观维度
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

-- 全市场流通市值 (由 stock_daily.circ_mv 汇总)
CREATE TABLE IF NOT EXISTS daily_circ_mv (
    trade_date TEXT PRIMARY KEY,
    total_circ_mv REAL
);

-- 成分股 PE/PB 中位数 (沪深300+中证500)
CREATE TABLE IF NOT EXISTS index_daily_pe (
    trade_date TEXT PRIMARY KEY,
    pe_med REAL,
    pb_med REAL,
    n_stocks INTEGER,
    const_date TEXT
);

-- AH溢价月表 (SSE AH Premium Index 月度值)
CREATE TABLE IF NOT EXISTS ah_premium_monthly (
    trade_date TEXT PRIMARY KEY,
    premium REAL,
    score REAL
);

-- 涨跌家数比预计算表 (由 stock_daily 汇总)
CREATE TABLE IF NOT EXISTS daily_updown (
    trade_date TEXT PRIMARY KEY,
    up_down_ratio REAL
);

-- 涨停/跌停预计算表 (由 stock_daily 汇总)
CREATE TABLE IF NOT EXISTS daily_limit (
    trade_date TEXT PRIMARY KEY,
    limit_up_ratio REAL,
    limit_ratio REAL
);

-- 破净率预计算表 (由 stock_daily 汇总)
CREATE TABLE IF NOT EXISTS daily_below_net (
    trade_date TEXT PRIMARY KEY,
    below_net_rate REAL
);

-- 均线排列比预计算表 (MA5>MA10>MA20>MA60 多头排列占比)
CREATE TABLE IF NOT EXISTS daily_ma_alignment (
    trade_date TEXT PRIMARY KEY,
    ma_alignment_ratio REAL
);

-- 历史成分股截面 (月末, 用于 PE/PB 中位数计算)
CREATE TABLE IF NOT EXISTS index_constituents_hist (
    index_code TEXT NOT NULL,
    con_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    weight REAL,
    PRIMARY KEY (index_code, con_code, trade_date)
);

-- 股权风险溢价预计算表 (ERP = 1/PE - 10Y国债)
CREATE TABLE IF NOT EXISTS daily_erp (
    trade_date TEXT PRIMARY KEY,
    erp REAL,
    ey REAL,
    bond_rate REAL,
    pe REAL
);

-- 宏观指标预计算表 (M1-M2 剪刀差, M2同比)
CREATE TABLE IF NOT EXISTS daily_macro (
    trade_date TEXT PRIMARY KEY,
    m1_yoy REAL,
    m2_yoy REAL,
    scissors REAL
);

-- GDP 季度数据 (Tushare cn_gdp)
CREATE TABLE IF NOT EXISTS gdp_quarterly (
    quarter TEXT PRIMARY KEY,       -- e.g. "2024Q1"
    gdp REAL,                      -- GDP 当季值 (亿元)
    gdp_yoy REAL,                  -- GDP 同比 (%)
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


def _migrate(conn, from_ver: int):
    """数据库版本迁移 — 按版本号逐步升级"""
    if from_ver < 2:
        pass
    if from_ver < 3:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(heat_index)").fetchall()}
        for col in ("dim_macro", "composite_score_smoothed", "heat_level", "heat_level_smoothed"):
            if col not in cols:
                conn.execute(f"ALTER TABLE heat_index ADD COLUMN {col} TEXT")
    if from_ver < 4:
        # 迁移: ah_premium 增加 n_stocks 列 (v3→v4)
        try:
            ah_cols = {r[1] for r in conn.execute("PRAGMA table_info(ah_premium)").fetchall()}
            if 'n_stocks' not in ah_cols:
                conn.execute("ALTER TABLE ah_premium ADD COLUMN n_stocks INTEGER")
            if 'created_at' not in ah_cols:
                conn.execute("ALTER TABLE ah_premium ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        except Exception as e:
            logger.warning("ah_premium migration skipped (table may not exist): %s", e)
    if from_ver < 5:
        # 迁移: index_daily_pe 增加 const_date 列 (v4→v5)
        try:
            pe_cols = {r[1] for r in conn.execute("PRAGMA table_info(index_daily_pe)").fetchall()}
            if 'const_date' not in pe_cols:
                conn.execute("ALTER TABLE index_daily_pe ADD COLUMN const_date TEXT")
        except Exception as e:
            logger.warning("index_daily_pe migration skipped (table may not exist): %s", e)
    logger.info("Database migrated from v%d to v%d", from_ver, SCHEMA_VERSION)


def init_database(db_path: str = None):
    """初始化数据库表结构 + 版本迁移"""
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        # 版本检查
        try:
            ver = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            current_ver = int(ver[0]) if ver else 1
        except Exception:
            current_ver = 1
        if current_ver < SCHEMA_VERSION:
            _migrate(conn, current_ver)
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value, updated_at) VALUES('schema_version', ?, datetime('now'))",
                (str(SCHEMA_VERSION),)
            )
    logger.info("Database initialized at %s (v%d)", db_path or DB_PATH, SCHEMA_VERSION)


# 配置：各预计算表的陈旧检测阈值
STALENESS_CONFIG = [
    {"table": "daily_updown",       "step": "S27", "fallback": True,  "max_gap_days": 5, "desc": "涨跌家数比"},
    {"table": "daily_limit",        "step": "S28", "fallback": True,  "max_gap_days": 5, "desc": "涨停/跌停"},
    {"table": "daily_below_net",    "step": "S29", "fallback": True,  "max_gap_days": 5, "desc": "破净率"},
    {"table": "daily_ma_alignment", "step": "S30", "fallback": False, "max_gap_days": 5, "desc": "MA排列比"},
    {"table": "daily_erp",          "step": "-",   "fallback": True,  "max_gap_days": 5, "desc": "股权风险溢价"},
    {"table": "daily_circ_mv",      "step": "S26", "fallback": False, "max_gap_days": 5, "desc": "流通市值"},
    {"table": "daily_macro",        "step": "-",   "fallback": False, "max_gap_days": 7, "desc": "宏观(M1-M2)"},
    {"table": "qvix_daily",         "step": "manual", "fallback": False, "max_gap_days": 5, "desc": "QVIX恐慌"},
    {"table": "index_daily_pe",     "step": "S25", "fallback": False, "max_gap_days": 5, "desc": "指数PE中位数"},
    {"table": "ah_premium_monthly", "step": "S4",  "fallback": True,  "max_gap_days": 38, "desc": "AH溢价(月)"},
]


def check_precompute_staleness(trade_date: str = None, db_path: str = None) -> list[dict]:
    """检查所有预计算表的最新日期，返回陈旧状态列表。

    每条记录包含:
      - table: 表名
      - desc: 中文描述
      - latest_date: 表中最新日期 (None = 无数据)
      - gap_days: 距目标交易日的日历天数差
      - max_gap_days: 允许的最大陈旧天数
      - stale: 是否陈旧（gap_days > max_gap_days）
      - has_fallback: 是否有实时 fallback 机制
      - step: 所属更新步骤
    """
    td = date.fromisoformat(trade_date) if trade_date else date.today()

    def _parse_date(s: str) -> Optional[date]:
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            # 处理 YYYY-MM 月格式 -> 映射到当月最后一天
            if len(s) == 7 and s[4] == '-':
                import calendar
                y, m = int(s[:4]), int(s[5:7])
                return date(y, m, calendar.monthrange(y, m)[1])
        return None

    results = []
    for cfg in STALENESS_CONFIG:
        latest_raw = get_latest_date(cfg["table"], db_path=db_path)
        latest_dt = _parse_date(latest_raw)
        gap = None
        if latest_dt:
            gap = (td - latest_dt).days
        stale = gap is not None and gap > cfg["max_gap_days"]
        results.append({
            "table": cfg["table"],
            "desc": cfg["desc"],
            "latest_date": latest_raw,
            "gap_days": gap,
            "max_gap_days": cfg["max_gap_days"],
            "stale": stale,
            "has_fallback": cfg["fallback"],
            "step": cfg["step"],
        })
    return results


_ALLOWED_TABLES = {
    "index_daily", "stock_daily", "stock_industry", "m2_monthly",
    "stock_market_cap", "margin_history", "northbound_history",
    "bond_yield", "index_pe_history", "ah_premium", "stock_balance",
    "limit_up_daily", "new_investors",
    "heat_index", "sector_heat", "metadata",
    "daily_circ_mv", "index_daily_pe", "ah_premium_monthly",
    "daily_updown", "daily_limit", "daily_ma_alignment",
    "daily_below_net", "daily_erp", "daily_macro", "daily_turnover", "qvix_daily",
    "stock_high_250d", "index_constituents_hist",
}


def save_dataframe(df: pd.DataFrame, table: str, db_path: str = None):
    """保存 DataFrame 到数据库（INSERT OR REPLACE upsert）"""
    if df.empty:
        return
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Table '{table}' not in allowlist")
    with get_conn(db_path) as conn:
        df.to_sql('_tmp_upsert', conn, if_exists='replace', index=False)
        cols = ', '.join(df.columns)
        pk = conn.execute(f"SELECT ltrim(sql, 'CREATE TABLE ') FROM sqlite_master WHERE type='table' AND name='{table}'").fetchone()
        if pk and 'PRIMARY KEY' in str(pk[0]).upper():
            conn.execute(f'INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM _tmp_upsert')
        else:
            conn.execute(f'INSERT INTO {table} ({cols}) SELECT {cols} FROM _tmp_upsert')
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


def save_heat_index_to_db(result: dict, db_path: str = None):
    """保存热度指数计算结果到数据库"""
    from src.output.json_writer import get_heat_level as _gl
    with get_conn(db_path) as conn:
        score = result.get("composite_score")
        smoothed = result.get("composite_score_smoothed")
        conn.execute("""
            INSERT OR REPLACE INTO heat_index
                (trade_date, composite_score, composite_score_smoothed,
                 heat_level, heat_level_smoothed,
                 dim_valuation, dim_macro, dim_fund, dim_sentiment,
                 dim_technical, dim_structure, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result.get("trade_date"),
            score,
            smoothed,
            _gl(score) if score is not None else None,
            _gl(smoothed) if smoothed is not None else None,
            result.get("dim_valuation"),
            result.get("dim_macro"),
            result.get("dim_fund"),
            result.get("dim_sentiment"),
            result.get("dim_technical"),
            result.get("dim_structure"),
            json.dumps(result.get("indicators", {}), ensure_ascii=False) if result.get("indicators") else None,
        ))
    logger.info("Saved heat index to DB: %s score=%s", result.get("trade_date"), score)


def update_index_daily_pe(trade_date: str, db_path: str = None):
    """计算指定交易日的成分股 PE/PB 中位数并写入 index_daily_pe 表"""
    with get_conn(db_path) as conn:
        const = conn.execute(
            "SELECT trade_date AS const_date, con_code FROM index_constituents_hist "
            "WHERE index_code IN ('hs300', 'zz500') "
            "AND trade_date = (SELECT MAX(trade_date) FROM index_constituents_hist WHERE trade_date <= ?)",
            (trade_date.replace("-", ""),)
        ).fetchall()
        if not const:
            logger.warning("update_index_daily_pe %s: no constituents found", trade_date)
            return False
        codes = [r[1] for r in const]
        placeholders = ",".join(["?" for _ in codes])
        df = pd.read_sql(
            f"SELECT peTTM, pbMRQ FROM stock_daily "
            f"WHERE trade_date=? AND stock_code IN ({placeholders})",
            conn, params=[trade_date] + codes
        )
        if df.empty:
            logger.warning("update_index_daily_pe %s: no stock_daily data", trade_date)
            return False
        pe_vals = pd.to_numeric(df["peTTM"], errors="coerce")
        pe_vals = pe_vals[(pe_vals > 0) & (pe_vals <= 500)].dropna()
        pb_vals = pd.to_numeric(df["pbMRQ"], errors="coerce")
        pb_vals = pb_vals[(pb_vals > 0) & (pb_vals <= 10)].dropna()
        const_date = const[0][0] if const else None
        conn.execute(
            "INSERT OR REPLACE INTO index_daily_pe (trade_date, pe_med, pb_med, n_stocks, const_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (trade_date,
             float(pe_vals.median()) if len(pe_vals) > 0 else None,
             float(pb_vals.median()) if len(pb_vals) > 0 else None,
             len(pe_vals),
             const_date)
        )
        logger.info("index_daily_pe %s: pe_med=%.2f pb_med=%.2f n=%d const=%s",
                     trade_date, pe_vals.median() if len(pe_vals) > 0 else 0,
                     pb_vals.median() if len(pb_vals) > 0 else 0, len(pe_vals), const_date)
        return True


def compute_daily_circ_mv(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 计算当日全市场流通市值并写入 daily_circ_mv"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT SUM(circ_mv) AS total_circ_mv FROM stock_daily WHERE trade_date=? AND circ_mv > 0",
            conn, params=[trade_date]
        )
        if df.empty or df.iloc[0]["total_circ_mv"] is None or df.iloc[0]["total_circ_mv"] <= 0:
            logger.warning("compute_daily_circ_mv %s: no valid circ_mv data", trade_date)
            return False
        total = float(df.iloc[0]["total_circ_mv"])
        conn.execute(
            "INSERT OR REPLACE INTO daily_circ_mv (trade_date, total_circ_mv) VALUES (?, ?)",
            (trade_date, total)
        )
        logger.info("daily_circ_mv %s: %.2f", trade_date, total)
        return True


def compute_daily_total_mv(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 计算当日全市场总市值并写入 stock_market_cap"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT SUM(total_mv) AS total_mv, COUNT(*) AS stock_count FROM stock_daily WHERE trade_date=? AND total_mv > 0",
            conn, params=[trade_date]
        )
        if df.empty or df.iloc[0]["total_mv"] is None or df.iloc[0]["total_mv"] <= 0:
            logger.warning("compute_daily_total_mv %s: no valid total_mv data", trade_date)
            return False
        total = float(df.iloc[0]["total_mv"])
        count = int(df.iloc[0]["stock_count"])
        conn.execute(
            "INSERT OR REPLACE INTO stock_market_cap (trade_date, total_mv, stock_count) VALUES (?, ?, ?)",
            (trade_date, total, count)
        )
        logger.info("stock_market_cap %s: total_mv=%.2f stocks=%d", trade_date, total, count)
        return True


def compute_daily_updown(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 计算当日涨跌家数比并写入 daily_updown"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT pct_change FROM stock_daily WHERE trade_date=? AND pct_change IS NOT NULL",
            conn, params=[trade_date]
        )
        if df.empty or len(df) < 100:
            logger.warning("compute_daily_updown %s: insufficient data (%d)", trade_date, len(df))
            return False
        up = (df["pct_change"] > 0).sum()
        dn = (df["pct_change"] < 0).sum()
        if dn == 0:
            logger.warning("compute_daily_updown %s: no down stocks", trade_date)
            return False
        ratio = round(up / dn, 6)
        conn.execute(
            "INSERT OR REPLACE INTO daily_updown (trade_date, up_down_ratio) VALUES (?, ?)",
            (trade_date, ratio)
        )
        logger.info("daily_updown %s: up=%d dn=%d ratio=%.4f", trade_date, up, dn, ratio)
        return True


def compute_daily_limit(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 计算当日涨停占比和涨跌停比并写入 daily_limit"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT pct_change FROM stock_daily WHERE trade_date=? AND pct_change IS NOT NULL",
            conn, params=[trade_date]
        )
        if df.empty or len(df) < 100:
            logger.warning("compute_daily_limit %s: insufficient data (%d)", trade_date, len(df))
            return False
        total = len(df)
        limit_up = int((df["pct_change"] >= 9.9).sum())
        limit_down = int((df["pct_change"] <= -9.9).sum())
        limit_up_ratio = round(limit_up / total, 6)
        limit_ratio = round(limit_up / limit_down, 6) if limit_down > 0 else None
        conn.execute(
            "INSERT OR REPLACE INTO daily_limit (trade_date, limit_up_ratio, limit_ratio) VALUES (?, ?, ?)",
            (trade_date, limit_up_ratio, limit_ratio)
        )
        logger.info("daily_limit %s: total=%d up=%d dn=%d up_ratio=%.4f ratio=%s",
                     trade_date, total, limit_up, limit_down, limit_up_ratio, limit_ratio)
        return True


def compute_daily_below_net(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 计算当日破净率并写入 daily_below_net"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT pbMRQ FROM stock_daily WHERE trade_date=? AND pbMRQ IS NOT NULL AND pbMRQ > 0",
            conn, params=[trade_date]
        )
        if df.empty or len(df) < 100:
            logger.warning("compute_daily_below_net %s: insufficient data (%d)", trade_date, len(df))
            return False
        total = len(df)
        below = int((df["pbMRQ"] < 1).sum())
        ratio = round(below / total, 6)
        conn.execute(
            "INSERT OR REPLACE INTO daily_below_net (trade_date, below_net_rate) VALUES (?, ?)",
            (trade_date, ratio)
        )
        logger.info("daily_below_net %s: total=%d below=%d rate=%.4f", trade_date, total, below, ratio)
        return True


def compute_daily_ma_alignment(trade_date: str, db_path: str = None) -> bool:
    """计算 MA5>MA10>MA20>MA60 多头排列占比并写入 daily_ma_alignment"""
    with get_conn(db_path) as conn:
        target = pd.read_sql(
            "SELECT stock_code FROM stock_daily WHERE trade_date=? AND close > 0",
            conn, params=[trade_date]
        )
        if target.empty or len(target) < 100:
            logger.warning("compute_daily_ma_alignment %s: insufficient stocks (%d)", trade_date, len(target))
            return False

        min_date = (pd.Timestamp(trade_date) - pd.DateOffset(days=400)).strftime("%Y-%m-%d")
        df = pd.read_sql(
            "SELECT stock_code, trade_date, close FROM stock_daily "
            "WHERE trade_date BETWEEN ? AND ? AND close > 0 "
            "ORDER BY stock_code, trade_date",
            conn, params=(min_date, trade_date)
        )

        def _check_alignment(group):
            group = group.sort_values("trade_date")
            s = group["close"].values
            if len(s) < 60:
                return 0
            ma5 = np.mean(s[-5:])
            ma10 = np.mean(s[-10:])
            ma20 = np.mean(s[-20:])
            ma60 = np.mean(s[-60:])
            return 1 if ma5 > ma10 > ma20 > ma60 else 0

        results = df.groupby("stock_code", sort=False).apply(_check_alignment)
        aligned = int(results.sum())
        total_stocks = len(results)
        ratio = round(aligned / total_stocks, 6) if total_stocks > 0 else 0

        conn.execute(
            "INSERT OR REPLACE INTO daily_ma_alignment (trade_date, ma_alignment_ratio) VALUES (?, ?)",
            (trade_date, ratio)
        )
        logger.info("daily_ma_alignment %s: aligned=%d/%d ratio=%.4f", trade_date, aligned, total_stocks, ratio)
        return True


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    init_database(path)
    print(f"Database initialized at {path}")
