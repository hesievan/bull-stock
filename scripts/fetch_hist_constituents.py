#!/usr/bin/env python3
"""
拉取沪深300 / 中证500 历史成分股截面, 写入 index_constituents_hist 表。

用途:
  src/data/database.update_index_daily_pe 依赖本表计算成分股 PE/PB 中位数
  (V2 估值维度 / ERP 的关键输入)。缺失本表会导致 index_daily_pe 全空,
  估值维度失效。

数据源: tushare index_weight (月末截面)
策略:
  - 每月拉取月末交易日成分股 (沪深300=000300.SH, 中证500=000905.SH)
  - 时间范围: 2015-01 ~ 当前 (逐月, 可断点续传)
  - trade_date 以 YYYYMMDD 存储 (与 update_index_daily_pe 查询一致)
  - con_code 转为 akshare 格式 (600519.SH -> sh600519)

用法:
  python scripts/fetch_hist_constituents.py
"""
import sys
import os
import time
import logging
import calendar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

logger = logging.getLogger(__name__)


def _load_env():
    if os.environ.get("TUSHARE_TOKEN"):
        return
    from pathlib import Path
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path.home() / "daily_stock_analysis" / ".env",
    ]
    for p in candidates:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("TUSHARE_TOKEN="):
                    os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip('"\'')
                    return


_load_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler(
                  os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "data", "fetch_hist_constituents.log"),
                  mode="a", encoding="utf-8")],
)
logger = logging.getLogger(__name__)

# index_code 必须与 update_index_daily_pe 的过滤 (IN ('hs300','zz500')) 一致
INDEX_MAP = {
    "000300.SH": "hs300",
    "000905.SH": "zz500",
}


def _to_ak_code(ts_code: str) -> str:
    """600519.SH -> sh600519"""
    if "." not in ts_code:
        return ts_code
    code, exch = ts_code.split(".")
    prefix = "sh" if exch == "SH" else "sz"
    return f"{prefix}{code}"


def _get_month_end_dates(pro, start_year: int = 2015, end_year: int = None, end_month: int = None):
    """获取每月最后一个交易日的日期 (YYYYMMDD)"""
    today = date.today()
    end_year = end_year or today.year
    end_month = end_month or today.month
    dates = []
    try:
        cal = pro.trade_cal(exchange="SSE",
                            start_date=f"{start_year}0101",
                            end_date=f"{end_year}{end_month:02d}28")
        cal = cal[cal["is_open"] == 1]
        for y in range(start_year, end_year + 1):
            m_max = end_month if y == end_year else 12
            for m in range(1, m_max + 1):
                month_cal = cal[cal["cal_date"].str.startswith(f"{y}{m:02d}")]
                if not month_cal.empty:
                    dates.append(month_cal["cal_date"].max())
    except Exception as e:
        logger.warning("trade_cal failed, fallback to calendar: %s", str(e)[:80])
        for y in range(start_year, end_year + 1):
            m_max = end_month if y == end_year else 12
            for m in range(1, m_max + 1):
                last_day = calendar.monthrange(y, m)[1]
                dates.append(f"{y}{m:02d}{last_day:02d}")
    return dates


def main():
    import tushare as ts
    import sqlite3
    import pandas as pd
    from src.data.database import DB_PATH, init_database, get_conn

    if not os.environ.get("TUSHARE_TOKEN"):
        raise SystemExit("TUSHARE_TOKEN 未设置")

    init_database()
    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])

    dates = _get_month_end_dates(pro)
    logger.info("拉取 %d 个月末 × %d 指数", len(dates), len(INDEX_MAP))

    with get_conn(DB_PATH) as conn:
        # 已有 (index_code, trade_date) 集合, 支持断点续传
        existing = {(r[0], r[1]) for r in conn.execute(
            "SELECT index_code, trade_date FROM index_constituents_hist").fetchall()}

        total = 0
        for i, dt in enumerate(dates):
            for idx_code, idx_name in INDEX_MAP.items():
                if (idx_name, dt) in existing:
                    continue
                try:
                    df = pro.index_weight(index_code=idx_code, start_date=dt, end_date=dt)
                    time.sleep(0.3)
                except Exception as e:
                    logger.warning("fetch %s %s 失败: %s", idx_code, dt, str(e)[:80])
                    continue
                if df is None or df.empty:
                    continue
                rows = [(idx_name, _to_ak_code(r["con_code"]), dt,
                         float(r["weight"]) if pd.notna(r.get("weight")) else None)
                        for _, r in df.iterrows()]
                conn.executemany(
                    "INSERT OR REPLACE INTO index_constituents_hist "
                    "(index_code, con_code, trade_date, weight) VALUES (?,?,?,?)",
                    rows,
                )
                total += len(rows)
            if (i + 1) % 12 == 0:
                logger.info("进度: %d/%d 月, 已写入 %d 行", i + 1, len(dates), total)

        # 校验
        stats = pd.read_sql(
            "SELECT index_code, COUNT(*) AS rows, COUNT(DISTINCT trade_date) AS dates, "
            "COUNT(DISTINCT con_code) AS stocks FROM index_constituents_hist GROUP BY index_code",
            conn,
        )
        logger.info("写入统计:\n%s", stats.to_string(index=False))

    logger.info("完成: 共 %d 行", total)


if __name__ == "__main__":
    main()
