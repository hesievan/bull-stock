#!/usr/bin/env python3
"""
中国 GDP 数据获取 (Tushare cn_gdp)

数据频率: 季度
覆盖范围: 2010年至今
字段: quarter, gdp, gdp_yoy, gdp_accumulate, gdp_accumulate_yoy

用法:
  python scripts/fetch_gdp.py                      # 拉取全部
  python scripts/fetch_gdp.py --since 2020         # 从指定年份开始
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = os.environ.get(
    "HEAT_INDEX_DB",
    os.path.join(os.path.dirname(__file__), "..", "data", "heat_index.db")
)


def fetch_gdp(since: str = None):
    """从 Tushare 拉取 GDP 季度数据"""
    import tushare as ts
    import sqlite3
    import pandas as pd

    # 加载 token
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        env_path = os.path.expanduser("~/.tushare/token")
        if os.path.exists(env_path):
            token = open(env_path).read().strip()
        else:
            logger.error("TUSHARE_TOKEN not set")
            return False

    pro = ts.pro_api(token)

    try:
        start_q = f"{since}0101" if since else "20000101"
        df = pro.cn_gdp(start_q=start_q)
    except Exception as e:
        logger.error("Failed to fetch GDP: %s", str(e)[:80])
        return False

    if df is None or df.empty:
        logger.warning("No GDP data returned")
        return False

    # 处理列名: Tushare 返回 quarter, gdp, gdp_yoy, pi, pi_yoy, si, si_yoy, ti, ti_yoy
    # gdp 单位为亿元(季度值), pi/si/ti 为三大产业
    df = df.rename(columns={
        "quarter": "quarter",
        "gdp": "gdp",
        "gdp_yoy": "gdp_yoy",
    })
    df["gdp"] = pd.to_numeric(df["gdp"], errors="coerce")
    df["gdp_yoy"] = pd.to_numeric(df["gdp_yoy"], errors="coerce")

    conn = sqlite3.connect(DB_PATH)
    written = 0
    for _, row in df.iterrows():
        try:
            conn.execute(
                """INSERT OR REPLACE INTO gdp_quarterly
                   (quarter, gdp, gdp_yoy)
                   VALUES (?, ?, ?)""",
                (
                    row["quarter"],
                    float(row["gdp"]) if pd.notna(row["gdp"]) else None,
                    float(row["gdp_yoy"]) if pd.notna(row["gdp_yoy"]) else None,
                )
            )
            written += 1
        except Exception as e:
            logger.warning("Failed to insert row %s: %s", row.get("quarter"), str(e)[:40])

    conn.commit()
    conn.close()
    logger.info("GDP data saved: %d rows", written)
    return True


if __name__ == "__main__":
    since = None
    if "--since" in sys.argv:
        idx = sys.argv.index("--since")
        if idx + 1 < len(sys.argv):
            since = sys.argv[idx + 1]
    fetch_gdp(since=since)
