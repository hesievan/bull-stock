#!/usr/bin/env python3
"""
中国 GDP 季度数据获取 (Tushare cn_gdp), 写入 gdp_quarterly 表。

用途:
  src/indicators/heat_index_v2.py 计算巴菲特指标时使用年度 GDP。
  quarter 字段需以 4 位年份开头 (如 2019Q1 / 201901), 由消费方取 quarter[:4]。

数据源: tushare cn_gdp (季度)
覆盖: 2010 年至今 (可断点续传)

用法:
  python scripts/fetch_gdp.py
  python scripts/fetch_gdp.py --since 2020
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
                               "data", "fetch_gdp.log"),
                  mode="a", encoding="utf-8")],
)
logger = logging.getLogger(__name__)


def fetch_gdp(since: str = None):
    """从 Tushare 拉取 GDP 季度数据并写入 gdp_quarterly"""
    import tushare as ts
    import sqlite3
    import pandas as pd
    from src.data.database import DB_PATH, init_database, get_conn

    if not os.environ.get("TUSHARE_TOKEN"):
        logger.error("TUSHARE_TOKEN 未设置")
        return False

    init_database()
    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])

    try:
        start_q = f"{since}0101" if since else "20000101"
        df = pro.cn_gdp(start_q=start_q)
    except Exception as e:
        logger.error("Failed to fetch GDP: %s", str(e)[:80])
        return False

    if df is None or df.empty:
        logger.warning("No GDP data returned")
        return False

    df["gdp"] = pd.to_numeric(df.get("gdp"), errors="coerce")
    df["gdp_yoy"] = pd.to_numeric(df.get("gdp_yoy"), errors="coerce")

    with get_conn(DB_PATH) as conn:
        written = 0
        for _, row in df.iterrows():
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO gdp_quarterly (quarter, gdp, gdp_yoy) VALUES (?, ?, ?)",
                    (
                        str(row["quarter"]),
                        float(row["gdp"]) if pd.notna(row["gdp"]) else None,
                        float(row["gdp_yoy"]) if pd.notna(row["gdp_yoy"]) else None,
                    ),
                )
                written += 1
            except Exception as e:
                logger.warning("Failed to insert row %s: %s", row.get("quarter"), str(e)[:40])
        conn.commit()

    logger.info("GDP data saved: %d rows", written)
    return True


if __name__ == "__main__":
    since = None
    if "--since" in sys.argv:
        idx = sys.argv.index("--since")
        if idx + 1 < len(sys.argv):
            since = sys.argv[idx + 1]
    fetch_gdp(since=since)
