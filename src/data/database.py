"""
本地 SQLite 数据库管理 (三源合一版)
数据源: tushare(全市场/融资融券/北向) + akshare(M2/AH溢价)
- 初始化表结构
- 增量数据写入 (INSERT OR REPLACE)
- 查询接口
"""

import sqlite3
import os
import logging
from datetime import date
from typing import Iterator, Optional
from contextlib import contextmanager

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("HEAT_INDEX_DB", os.path.join(os.path.dirname(__file__), "..", "..", "data", "heat_index.db"))

# ── 建表 SQL ──────────────────────────────────────────────────────────────────
SCHEMA_VERSION = 14

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

-- 个股行业分类 (tushare stock_basic) — code 格式 sh600000, 匹配 stock_daily.stock_code
CREATE TABLE IF NOT EXISTS stock_industry (
    code TEXT NOT NULL,           -- sh600000 格式, 匹配 stock_daily.stock_code
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

-- M1月度货币供应量 (akshare: macro_china_money_supply) — 用于 m1_m2_spread
CREATE TABLE IF NOT EXISTS m1_monthly (
    month       TEXT PRIMARY KEY,
    m1_billion  REAL,
    m1_yoy      REAL
);

-- A股总市值 (stock_daily total_mv 成分股加总proxy)
CREATE TABLE IF NOT EXISTS stock_market_cap (
    trade_date  TEXT PRIMARY KEY,
    total_mv    REAL,
    stock_count INTEGER
);

-- [已删除] V1遗留/死表: stock_balance

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

-- [已删除] V1遗留/死表: limit_up_daily
-- [已删除] V1遗留/死表: ah_premium
-- [已删除] V1遗留/死表: new_investors
-- [已删除] V1遗留/死表: heat_index (V1 结果表, 已被 JSON 输出取代)
-- [已删除] V1遗留/死表: sector_heat

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

-- [已删除] V1遗留/死表: ah_premium_monthly

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

-- 250日新高占比预计算表 (由 stock_daily 汇总, 用于 new_high 指标; F8修复:
-- 逐日实时计算 O(n×250) 在10年回测/CI种子库构建中耗时小时级, 故预计算)
CREATE TABLE IF NOT EXISTS daily_new_high (
    trade_date TEXT PRIMARY KEY,
    new_high_ratio REAL,     -- 当日250日新高占比 (0~1)
    n_stocks INTEGER         -- 参与计算的股票数
);

-- 换手率预计算表 (F3: turnover 指标 10 年窗口加速, 口径=Σamount/Σcirc_mv×10)
CREATE TABLE IF NOT EXISTS daily_turnover (
    trade_date TEXT PRIMARY KEY,
    turnover_rate REAL       -- 全市场换手率(%)
);

-- 历史成分股截面 (月末, 用于 PE/PB 中位数计算)
CREATE TABLE IF NOT EXISTS index_constituents_hist (
    index_code TEXT NOT NULL,
    con_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    weight REAL,
    PRIMARY KEY (index_code, con_code, trade_date)
);

-- [已删除] V1遗留/死表: daily_erp (ERP 已被 PE+巴菲特取代)
-- [已删除] V1遗留/死表: daily_macro (M1-M2 改用 m1_monthly/m2_monthly)

-- 申万行业分类 (tushare stock_basic.industry)
CREATE TABLE IF NOT EXISTS stock_shenwan (
    stock_code TEXT NOT NULL,
    sw_code TEXT,               -- 申万一级行业代码
    sw_name TEXT,               -- 申万一级行业名称
    sw_l2_code TEXT,            -- 申万二级细分行业代码 (如 801125 白酒, 801194 保险)
    sw_l2_name TEXT,            -- 申万二级细分行业名称
    update_date TEXT,
    PRIMARY KEY (stock_code)
);

-- QVIX 恐慌指数日度量 (ATR-25 + 认购溢价) — 指标: qvix_50/300/1000, panic_index, concentration
CREATE TABLE IF NOT EXISTS qvix_daily (
    trade_date TEXT NOT NULL PRIMARY KEY,
    qvix REAL,                  -- QVIX 恐慌指数
    qvix_50 REAL,               -- 上证50 子品种
    qvix_300 REAL,              -- 沪深300 子品种
    qvix_1000 REAL,             -- 中证1000 子品种
    panic_index REAL,           -- 恐慌指数 (综合)
    concentration REAL          -- 集中度
);

-- GDP 季度数据 (Tushare cn_gdp)
CREATE TABLE IF NOT EXISTS gdp_quarterly (
    quarter TEXT PRIMARY KEY,       -- e.g. "2024Q1"
    gdp REAL,                      -- GDP 当季值 (亿元)
    gdp_yoy REAL,                  -- GDP 同比 (%)
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 涨停封板率 (tushare limit_list 聚合, P0-1)
CREATE TABLE IF NOT EXISTS daily_seal_rate (
    trade_date TEXT PRIMARY KEY,
    seal_rate REAL,                -- 封板率 = sealed_count / limit_up_count
    limit_up_count INTEGER,        -- 触板涨停数 (limit=='U')
    sealed_count INTEGER           -- 封板成功数 (open_times==0)
);

-- 南向通当日净买额 (akshare stock_hsgt_hist_em '南向资金', 单位亿元)
-- P1.2 (2026-09): 补 north_ratio 退役后的跨境资金信号; 南向 2024-08 后仍正常披露
CREATE TABLE IF NOT EXISTS daily_hsgt_south (
    trade_date TEXT NOT NULL PRIMARY KEY,
    south_net REAL                 -- 当日成交净买额 (亿元)
);

-- 股指期货基差 (akshare futures_main_sina IF0 主力连续 + index_daily sh000300 现货)
-- P1.3 (2026-09): basis_rate = (IF主力收盘 - 沪深300现货收盘) / 沪深300现货收盘
-- 注: 主力连续在换月日存在小幅跳变 (移仓成本), 百分位口径下可接受
CREATE TABLE IF NOT EXISTS daily_futures_basis (
    trade_date TEXT NOT NULL PRIMARY KEY,
    fut_close REAL,                -- IF 主力连续收盘价
    spot_close REAL,               -- 沪深300 现货收盘价
    basis_rate REAL                -- (fut-spot)/spot, 正=升水 负=贴水
);

-- 新增投资者开户数 (月频, akshare stock_account_statistics_em, 中国结算)
-- P3 (2026-09): 散户 FOMO 低频锚, 仅展示不入分 (月频不硬塞日频合成)
-- 注意: 中国结算/东财源 2023-08 后停止更新, 展示时附月份标签
CREATE TABLE IF NOT EXISTS monthly_accounts (
    month TEXT NOT NULL PRIMARY KEY,   -- YYYY-MM
    new_accounts REAL                  -- 新增投资者数量 (万户)
);

-- 宽基 ETF 总份额日度快照 (akshare fund_etf_spot_em, P3 数据收集起点)
-- P3 (2026-09): 份额历史无法免费回填, 自采集日起积累; 足够历史后再评估入分
CREATE TABLE IF NOT EXISTS daily_etf_flow (
    trade_date TEXT NOT NULL PRIMARY KEY,
    total_shares REAL,                 -- 跟踪的宽基 ETF 份额合计 (亿份)
    n_funds INTEGER                    -- 参与统计的 ETF 只数
);

-- ── 性能索引 (v12) ──────────────────────────────────────────────────────────
-- 重点行业热度查询: 按 sw_code/sw_l2_code 过滤, 并经 stock_code JOIN stock_daily
-- 取 365 天回看历史; 缺索引时退化为全表扫描。
CREATE INDEX IF NOT EXISTS idx_shenwan_sw_code  ON stock_shenwan(sw_code);
CREATE INDEX IF NOT EXISTS idx_shenwan_sw_l2    ON stock_shenwan(sw_l2_code);
CREATE INDEX IF NOT EXISTS idx_daily_stock_code ON stock_daily(stock_code, trade_date);
"""


@contextmanager
def get_conn(db_path: str = None) -> Iterator[sqlite3.Connection]:
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


def _migrate(conn: sqlite3.Connection, from_ver: int) -> None:
    """数据库版本迁移 — 按版本号逐步升级"""
    if from_ver < 2:
        pass
    # [已删除] v3/v4 迁移: 原引用已删除的 heat_index/ah_premium 表, 不再需要
    if from_ver < 5:
        # 迁移: index_daily_pe 增加 const_date 列 (v4→v5)
        try:
            pe_cols = {r[1] for r in conn.execute("PRAGMA table_info(index_daily_pe)").fetchall()}
            if "const_date" not in pe_cols:
                conn.execute("ALTER TABLE index_daily_pe ADD COLUMN const_date TEXT")
        except Exception as e:
            logger.warning("index_daily_pe migration skipped (table may not exist): %s", e)
    if from_ver < 6:
        # 迁移 v6: qvix_daily 增加成分列 — 恐慌指数的三个子品种 + 集中度
        try:
            qvix_cols = {r[1] for r in conn.execute("PRAGMA table_info(qvix_daily)").fetchall()}
            for col_name in ("qvix_50", "qvix_300", "qvix_1000", "panic_index", "concentration"):
                if col_name not in qvix_cols:
                    conn.execute(f"ALTER TABLE qvix_daily ADD COLUMN {col_name} REAL")
            logger.info("qvix_daily migrated: added component columns")
        except Exception as e:
            logger.warning("qvix_daily migration skipped: %s", e)
    # [已删除] v7 迁移: 原 CREATE heat_index_v7 ... RENAME heat_index, 引用已删除的 heat_index 表
    if from_ver < 8:
        # 迁移 v8: 新建 stock_shenwan 表（SCHEMA 已包含建表 DDL，此处只打日志）
        logger.info("v8 migration: stock_shenwan table added (populated by S3_shenwan step)")
    if from_ver < 9:
        # 迁移 v9: daily_circ_mv 去重 — 旧版无 PRIMARY KEY 导致大量重复行 (11173→2804)
        try:
            before = conn.execute("SELECT COUNT(*) FROM daily_circ_mv").fetchone()[0]
            conn.executescript("""
                CREATE TABLE daily_circ_mv_v9 (
                    trade_date TEXT PRIMARY KEY,
                    total_circ_mv REAL
                );
                INSERT OR REPLACE INTO daily_circ_mv_v9
                    SELECT trade_date, MAX(total_circ_mv) FROM daily_circ_mv GROUP BY trade_date;
                DROP TABLE daily_circ_mv;
                ALTER TABLE daily_circ_mv_v9 RENAME TO daily_circ_mv;
            """)
            after = conn.execute("SELECT COUNT(*) FROM daily_circ_mv").fetchone()[0]
            logger.info("v9 migration: daily_circ_mv dedup %d→%d rows", before, after)
        except Exception as e:
            logger.warning("daily_circ_mv dedup skipped: %s", e)
    if from_ver < 10:
        # v10: 新增 daily_seal_rate 表 (涨停封板率, P0-1)
        # 表已由 SCHEMA 的 CREATE TABLE IF NOT EXISTS 自动创建, 此处仅记录
        logger.info("v10 migration: daily_seal_rate table added (涨停封板率)")
    if from_ver < 11:
        # v11: stock_shenwan 增加二级细分行业列 (sw_l2_code/sw_l2_name)，支持白酒/保险等二级行业
        try:
            sw_cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_shenwan)").fetchall()}
            for col_name, col_type in (("sw_l2_code", "TEXT"), ("sw_l2_name", "TEXT")):
                if col_name not in sw_cols:
                    conn.execute(f"ALTER TABLE stock_shenwan ADD COLUMN {col_name} {col_type}")
            logger.info("v11 migration: stock_shenwan added l2 columns (sw_l2_code/sw_l2_name)")
        except Exception as e:
            logger.warning("stock_shenwan l2 migration skipped: %s", e)
    if from_ver < 12:
        # v12: 性能索引。重点行业热度查询 (focus_industries) 按 sw_code/sw_l2_code 过滤
        # 并通过 stock_code JOIN stock_daily 取 365 天回看历史, 缺索引时全表扫描。
        try:
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_shenwan_sw_code  ON stock_shenwan(sw_code);
                CREATE INDEX IF NOT EXISTS idx_shenwan_sw_l2    ON stock_shenwan(sw_l2_code);
                CREATE INDEX IF NOT EXISTS idx_daily_stock_code ON stock_daily(stock_code, trade_date);
            """)
            logger.info(
                "v12 migration: added performance indexes (stock_shenwan.sw_code/sw_l2_code, stock_daily.stock_code)"
            )
        except Exception as e:
            logger.warning("v12 index migration skipped: %s", e)
    logger.info("Database migrated from v%d to v%d", from_ver, SCHEMA_VERSION)


def init_database(db_path: str = None) -> None:
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
                (str(SCHEMA_VERSION),),
            )
    logger.info("Database initialized at %s (v%d)", db_path or DB_PATH, SCHEMA_VERSION)


# 配置：各预计算表的陈旧检测阈值
STALENESS_CONFIG = [
    {"table": "daily_updown", "step": "S27", "fallback": True, "max_gap_days": 5, "desc": "涨跌家数比"},
    {"table": "daily_limit", "step": "S28", "fallback": True, "max_gap_days": 5, "desc": "涨停/跌停"},
    {"table": "daily_below_net", "step": "S29", "fallback": True, "max_gap_days": 5, "desc": "破净率"},
    {"table": "daily_ma_alignment", "step": "S30", "fallback": False, "max_gap_days": 5, "desc": "MA排列比"},
    {"table": "daily_new_high", "step": "S30b", "fallback": True, "max_gap_days": 5, "desc": "创新高占比"},
    {"table": "daily_turnover", "step": "S30c", "fallback": True, "max_gap_days": 5, "desc": "换手率(10年窗口)"},
    {"table": "daily_seal_rate", "step": "S31b", "fallback": True, "max_gap_days": 5, "desc": "涨停封板率"},
    {"table": "daily_circ_mv", "step": "S26", "fallback": False, "max_gap_days": 5, "desc": "流通市值"},
    {"table": "daily_hsgt_south", "step": "S24f", "fallback": True, "max_gap_days": 5, "desc": "南向净买额"},
    {"table": "daily_futures_basis", "step": "S24g", "fallback": True, "max_gap_days": 5, "desc": "IF基差"},
    {"table": "qvix_daily", "step": "manual", "fallback": False, "max_gap_days": 5, "desc": "QVIX恐慌"},
    {"table": "index_daily_pe", "step": "S25", "fallback": False, "max_gap_days": 5, "desc": "指数PE中位数"},
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
            if len(s) == 7 and s[4] == "-":
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
        # ISSUE-8 修复: 空表(完全缺失数据)应判定为 stale=True, 而非 False
        stale = (latest_dt is None) or (gap is not None and gap > cfg["max_gap_days"])
        results.append(
            {
                "table": cfg["table"],
                "desc": cfg["desc"],
                "latest_date": latest_raw,
                "gap_days": gap,
                "max_gap_days": cfg["max_gap_days"],
                "stale": stale,
                "has_fallback": cfg["fallback"],
                "step": cfg["step"],
            }
        )
    return results


_ALLOWED_TABLES = {
    "index_daily",
    "stock_daily",
    "stock_industry",
    "m2_monthly",
    "stock_market_cap",
    "margin_history",
    "northbound_history",
    "bond_yield",
    "index_pe_history",
    "metadata",
    "daily_circ_mv",
    "index_daily_pe",
    "m1_monthly",
    "daily_updown",
    "daily_limit",
    "daily_ma_alignment",
    "daily_below_net",
    "daily_turnover",
    "qvix_daily",
    "daily_new_high",
    "stock_high_250d",
    "index_constituents_hist",
    "stock_shenwan",
    "daily_seal_rate",
    "daily_hsgt_south",
    "daily_futures_basis",
    "monthly_accounts",
    "daily_etf_flow",
}


def save_dataframe(df: pd.DataFrame, table: str, db_path: str = None) -> None:
    """保存 DataFrame 到数据库（INSERT OR REPLACE upsert）"""
    if df.empty:
        return
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Table '{table}' not in allowlist")
    # 校验列名合法（仅允许字母/数字/下划线），并加引号，避免注入或含空格列名导致 SQL 错误
    import re

    safe_cols = []
    for c in df.columns:
        if not isinstance(c, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", c):
            raise ValueError(f"Invalid column name rejected: {c!r}")
        safe_cols.append(f'"{c}"')
    cols = ", ".join(safe_cols)
    with get_conn(db_path) as conn:
        # ISSUE-10 修复: 固定临时表名并发冲突, 使用带时间戳的唯一表名
        import uuid as _uuid

        tmp_name = f"_tmp_upsert_{_uuid.uuid4().hex[:8]}"
        df.to_sql(tmp_name, conn, if_exists="replace", index=False)
        pk = conn.execute(
            f"SELECT ltrim(sql, 'CREATE TABLE ') FROM sqlite_master WHERE type='table' AND name='{table}'"
        ).fetchone()
        if pk and "PRIMARY KEY" in str(pk[0]).upper():
            conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM {tmp_name}")
        else:
            conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {tmp_name}")
        conn.execute(f"DROP TABLE {tmp_name}")
    logger.info("Saved %d rows to %s", len(df), table)


def read_dataframe(query: str, params=None, db_path: str = None) -> pd.DataFrame:
    """从数据库读取 DataFrame"""
    with get_conn(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_latest_date(table: str, date_col: str = "trade_date", db_path: str = None) -> Optional[str]:
    """获取最新日期"""
    with get_conn(db_path) as conn:
        row = conn.execute(f"SELECT MAX({date_col}) as d FROM {table}").fetchone()
    return row["d"] if row and row["d"] else None


# [已删除] save_heat_index_to_db: 写入已删除的 V1 heat_index 表; V2 结果改由 json_writer.save_results_v2 输出 JSON
# 如需把结果落库, 可新建一张与 V2 四维结构一致的结果表, 但当前以 web/data/*.json 为准。


def update_index_daily_pe(trade_date: str, db_path: str = None) -> bool | None:
    """计算指定交易日的成分股 PE/PB 中位数并写入 index_daily_pe 表"""
    with get_conn(db_path) as conn:
        const = conn.execute(
            "SELECT trade_date AS const_date, con_code FROM index_constituents_hist "
            "WHERE index_code IN ('hs300', 'zz500') "
            "AND trade_date = (SELECT MAX(trade_date) FROM index_constituents_hist WHERE trade_date <= ?)",
            (trade_date,),
        ).fetchall()
        if not const:
            logger.warning("update_index_daily_pe %s: no constituents found", trade_date)
            return False
        codes = [r[1] for r in const]
        placeholders = ",".join(["?" for _ in codes])
        df = pd.read_sql(
            f"SELECT peTTM, pbMRQ FROM stock_daily WHERE trade_date=? AND stock_code IN ({placeholders})",
            conn,
            params=[trade_date] + codes,
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
            (
                trade_date,
                float(pe_vals.median()) if len(pe_vals) > 0 else None,
                float(pb_vals.median()) if len(pb_vals) > 0 else None,
                len(pe_vals),
                const_date,
            ),
        )
        logger.info(
            "index_daily_pe %s: pe_med=%.2f pb_med=%.2f n=%d const=%s",
            trade_date,
            pe_vals.median() if len(pe_vals) > 0 else 0,
            pb_vals.median() if len(pb_vals) > 0 else 0,
            len(pe_vals),
            const_date,
        )
        return True


def compute_daily_circ_mv(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 计算当日全市场流通市值并写入 daily_circ_mv"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT SUM(circ_mv) AS total_circ_mv FROM stock_daily WHERE trade_date=? AND circ_mv > 0",
            conn,
            params=[trade_date],
        )
        if df.empty or df.iloc[0]["total_circ_mv"] is None or df.iloc[0]["total_circ_mv"] <= 0:
            logger.warning("compute_daily_circ_mv %s: no valid circ_mv data", trade_date)
            return False
        total = float(df.iloc[0]["total_circ_mv"])
        conn.execute(
            "INSERT OR REPLACE INTO daily_circ_mv (trade_date, total_circ_mv) VALUES (?, ?)", (trade_date, total)
        )
        logger.info("daily_circ_mv %s: %.2f", trade_date, total)
        return True


def compute_daily_total_mv(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 计算当日全市场总市值并写入 stock_market_cap"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT SUM(total_mv) AS total_mv, COUNT(*) AS stock_count FROM stock_daily WHERE trade_date=? AND total_mv > 0",
            conn,
            params=[trade_date],
        )
        if df.empty or df.iloc[0]["total_mv"] is None or df.iloc[0]["total_mv"] <= 0:
            logger.warning("compute_daily_total_mv %s: no valid total_mv data", trade_date)
            return False
        total = float(df.iloc[0]["total_mv"])
        count = int(df.iloc[0]["stock_count"])
        conn.execute(
            "INSERT OR REPLACE INTO stock_market_cap (trade_date, total_mv, stock_count) VALUES (?, ?, ?)",
            (trade_date, total, count),
        )
        logger.info("stock_market_cap %s: total_mv=%.2f stocks=%d", trade_date, total, count)
        return True


def compute_daily_updown(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 计算当日涨跌家数比并写入 daily_updown"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT pct_change FROM stock_daily WHERE trade_date=? AND pct_change IS NOT NULL",
            conn,
            params=[trade_date],
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
            "INSERT OR REPLACE INTO daily_updown (trade_date, up_down_ratio) VALUES (?, ?)", (trade_date, ratio)
        )
        logger.info("daily_updown %s: up=%d dn=%d ratio=%.4f", trade_date, up, dn, ratio)
        return True


def compute_daily_limit(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 计算当日涨停占比和涨跌停比并写入 daily_limit"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT pct_change FROM stock_daily WHERE trade_date=? AND pct_change IS NOT NULL",
            conn,
            params=[trade_date],
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
            (trade_date, limit_up_ratio, limit_ratio),
        )
        logger.info(
            "daily_limit %s: total=%d up=%d dn=%d up_ratio=%.4f ratio=%s",
            trade_date,
            total,
            limit_up,
            limit_down,
            limit_up_ratio,
            limit_ratio,
        )
        return True


def compute_daily_below_net(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 计算当日破净率并写入 daily_below_net"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT pbMRQ FROM stock_daily WHERE trade_date=? AND pbMRQ IS NOT NULL AND pbMRQ > 0",
            conn,
            params=[trade_date],
        )
        if df.empty or len(df) < 100:
            logger.warning("compute_daily_below_net %s: insufficient data (%d)", trade_date, len(df))
            return False
        total = len(df)
        below = int((df["pbMRQ"] < 1).sum())
        ratio = round(below / total, 6)
        conn.execute(
            "INSERT OR REPLACE INTO daily_below_net (trade_date, below_net_rate) VALUES (?, ?)", (trade_date, ratio)
        )
        logger.info("daily_below_net %s: total=%d below=%d rate=%.4f", trade_date, total, below, ratio)
        return True


def compute_daily_ma_alignment(trade_date: str, db_path: str = None) -> bool:
    """计算 MA5>MA10>MA20>MA60 多头排列占比并写入 daily_ma_alignment"""
    with get_conn(db_path) as conn:
        target = pd.read_sql(
            "SELECT stock_code FROM stock_daily WHERE trade_date=? AND close > 0", conn, params=[trade_date]
        )
        if target.empty or len(target) < 100:
            logger.warning("compute_daily_ma_alignment %s: insufficient stocks (%d)", trade_date, len(target))
            return False

        min_date = (pd.Timestamp(trade_date) - pd.DateOffset(days=400)).strftime("%Y-%m-%d")
        df = pd.read_sql(
            "SELECT stock_code, trade_date, close FROM stock_daily "
            "WHERE trade_date BETWEEN ? AND ? AND close > 0 "
            "ORDER BY stock_code, trade_date",
            conn,
            params=(min_date, trade_date),
        )

        def _check_alignment(group: pd.DataFrame) -> int:
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
            (trade_date, ratio),
        )
        logger.info("daily_ma_alignment %s: aligned=%d/%d ratio=%.4f", trade_date, aligned, total_stocks, ratio)
        return True


def compute_daily_new_high(trade_date: str, db_path: str = None) -> bool:
    """计算 250日新高占比并写入 daily_new_high (F8修复预计算表)"""
    with get_conn(db_path) as conn:
        target = pd.read_sql(
            "SELECT stock_code, close FROM stock_daily WHERE trade_date=? AND close > 0", conn, params=[trade_date]
        )
        if target.empty or len(target) < 100:
            logger.warning("compute_daily_new_high %s: insufficient stocks (%d)", trade_date, len(target))
            return False

        # BUG-6 修复: 250个交易日 ≈ 365个日历日 (原 250 日历日仅约 178 交易日)
        min_date = (pd.Timestamp(trade_date) - pd.DateOffset(days=365)).strftime("%Y-%m-%d")
        hist = pd.read_sql(
            "SELECT stock_code, MAX(close) as max_close FROM stock_daily "
            "WHERE trade_date BETWEEN ? AND ? AND close > 0 GROUP BY stock_code",
            conn,
            params=(min_date, trade_date),
        )
        if hist.empty:
            return False

        merged = target.merge(hist, on="stock_code", how="inner").dropna()
        if len(merged) < 100:
            return False

        # 阈值与 heat_index_v2.NEW_HIGH_THRESHOLD(0.98) 保持一致 (2%容差)
        new_high = int((merged["close"] >= merged["max_close"] * 0.98).sum())
        ratio = round(new_high / len(merged), 6)

        conn.execute(
            "INSERT OR REPLACE INTO daily_new_high (trade_date, new_high_ratio, n_stocks) VALUES (?, ?, ?)",
            (trade_date, ratio, len(merged)),
        )
        logger.info("daily_new_high %s: new_high=%d/%d ratio=%.4f", trade_date, new_high, len(merged), ratio)
        return True


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    init_database(path)
    print(f"Database initialized at {path}")
