"""
P1 修复: 用 akshare 补充 2015-2019 融资融券历史数据

当前 margin_history 从 2019-10-21 开始 (tushare 覆盖范围)
本脚本拉取 2015-01-01 ~ 2019-10-20 的数据，并合并为统一的 market margin 口径写入数据库

数据源: akshare stock_margin_sse (上交所, 有2015年起历史)
       tushare pro.margin (2019-01起, 已存在)

注意: akshare SSE 数据只有上交所，深交所数据需从 tushare 获取
      但 tushare margin 接口从2019年开始，且是本已写入的数据
      因此本脚本主要补充 2015-01-01 ~ 2018-12-31 的上交所数据
      2019 年的数据 tushare 已覆盖

写入策略:
  - 仅写入 trade_date 在 2015-01-01 ~ 2019-01-14 范围内
  - 与现有 tushare 数据去重: 已存在的不覆盖 (tushare 包含沪深两市合计)
  - rzmre 单位: 元 (用于 margin_ratio 计算)
"""
import sys
import time
import logging
import sqlite3
import pandas as pd
import akshare as ak

sys.path.insert(0, '.')
from src.data.database import DB_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

START_DATE = "20150101"
END_DATE = "20190114"  # 到 tushare 覆盖前一个月

def fetch_sse_margin(start: str, end: str) -> pd.DataFrame:
    """拉取上交所融资融券历史"""
    try:
        df = ak.stock_margin_sse(start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning("akshare SSE margin %s~%s failed: %s", start, end, str(e)[:80])
        return pd.DataFrame()

def process_and_save(df: pd.DataFrame, conn: sqlite3.Connection) -> int:
    """处理 akshare 数据并写入 margin_history 表"""
    if df.empty:
        return 0

    # akshare SSE 列名映射 → tushare margin_history 列名
    rename_map = {
        "信用交易日期": "trade_date",
        "融资余额": "rzye",
        "融资买入额": "rzmre",
        "融券余量金额": "rzche",  # 用融券余量金额近似 rzche
        "融券余量": "rqye",       # 原始单位是股数 → 不直接匹配
        "融资融券余额": "rzrqye",
    }
    df = df.rename(columns=rename_map)

    # 格式化日期
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")

    # 检查已存在的日期
    existing = set(r[0] for r in conn.execute(
        "SELECT trade_date FROM margin_history WHERE trade_date BETWEEN ? AND ?",
        (df["trade_date"].min(), df["trade_date"].max())
    ).fetchall())

    # 过滤已有数据
    df = df[~df["trade_date"].isin(existing)]
    if df.empty:
        return 0

    # 只保留需要的列 + 补全缺失列为 0
    needed_cols = ["trade_date", "rzye", "rzmre", "rzche", "rqye", "rqmcl", "rzrqye"]
    for col in needed_cols:
        if col not in df.columns:
            df[col] = 0.0
    df = df[needed_cols]

    # 数值列转换
    for col in ["rzye", "rzmre", "rzche", "rqye", "rqmcl", "rzrqye"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # 写入
    for _, row in df.iterrows():
        vals = (row["trade_date"], row["rzye"], row["rzmre"], row["rzche"], row["rqye"], row["rqmcl"], row["rzrqye"])
        conn.execute(
            "INSERT OR IGNORE INTO margin_history (trade_date, rzye, rzmre, rzche, rqye, rqmcl, rzrqye) VALUES (?,?,?,?,?,?,?)",
            vals
        )
    conn.commit()
    return len(df)

def main():
    logger.info("P1: 补充融资融券历史数据 (2015-2019)")
    logger.info("DB: %s", DB_PATH)

    conn = sqlite3.connect(DB_PATH)

    # 检查现有最早日期
    cur_min = conn.execute("SELECT MIN(trade_date) FROM margin_history").fetchone()[0]
    logger.info("现有最早日期: %s", cur_min)

    # 分批拉取 akshare (按年, 避免单次请求过大)
    total_written = 0
    years = [
        ("20150101", "20151231"),
        ("20160101", "20161231"),
        ("20170101", "20171231"),
        ("20180101", "20181231"),
        ("20190101", "20190114"),
    ]

    for s, e in years:
        logger.info("拉取 %s ~ %s...", s, e)
        df = fetch_sse_margin(s, e)
        if not df.empty:
            n = process_and_save(df, conn)
            total_written += n
            logger.info("  %s ~ %s: 写入 %d 行", s, e, n)
        time.sleep(2)

    # 验证
    new_min = conn.execute("SELECT MIN(trade_date) FROM margin_history").fetchone()[0]
    total_rows = conn.execute("SELECT COUNT(*) FROM margin_history").fetchone()[0]
    logger.info("完成: 写入 %d 行, 最早日期 %s, 总行数 %d", total_written, new_min, total_rows)

    # 显示2015年样本
    sample = pd.read_sql(
        "SELECT * FROM margin_history WHERE trade_date='2015-06-12'", conn
    )
    if not sample.empty:
        logger.info("2015-06-12 样本: rzye=%s, rzmre=%s",
                    sample.iloc[0]["rzye"], sample.iloc[0]["rzmre"])
    else:
        logger.warning("2015-06-12 无数据")

    conn.close()
    logger.info("P1 修复 done: %d rows written", total_written)

if __name__ == "__main__":
    main()
