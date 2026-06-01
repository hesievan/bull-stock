"""
拉取恒生AH股溢价指数 (HSAHP) 历史数据
数据源: 东方财富 push2his 接口 (通过 curl 调用，避免 Python requests 被封锁)
secid=100.HSAHP → 字段: 日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
"""
import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "heat_index.db"
EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def fetch_hsahp(limit: int = 8000) -> pd.DataFrame:
    """从东方财富拉取 HSAHP 日线 (通过 curl)

    东方财富可能间歇性封锁 IP，失败时等待后重试，最多 5 次
    """
    params = (
        f"secid=100.HSAHP"
        f"&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&end=20500101&lmt={limit}"
    )
    url = f"{EASTMONEY_URL}?{params}"

    klines = []
    for attempt in range(5):
        try:
            result = subprocess.run(
                [
                    "curl", "-s", "--max-time", "30",
                    "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "-H", "Referer: https://quote.eastmoney.com/",
                    url,
                ],
                capture_output=True, text=True,
            )
            if result.returncode != 0 or not result.stdout.strip():
                raise ConnectionError(f"curl rc={result.returncode}")
            data = json.loads(result.stdout)
            klines = (data.get("data") or {}).get("klines") or []
            if klines:
                break
            raise ValueError("空数据")
        except Exception as e:
            if attempt < 4:
                wait = 20 if attempt < 2 else 40
                print(f"  重试 {attempt+1}/5 ({wait}s): {str(e)[:60]}")
                time.sleep(wait)
            else:
                raise

    rows = []
    for line in klines:
        parts = line.split(",")
        rows.append(
            {
                "trade_date": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
            }
        )

    return pd.DataFrame(rows)


def main():
    print("正在拉取恒生AH溢价指数 (HSAHP) ...")
    df = fetch_hsahp()
    print(f"  获取 {len(df)} 条, {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = pd.read_sql("SELECT COUNT(*) as n FROM ah_premium", conn).iloc[0, 0]
        print(f"  现有数据: {existing} 行")

        # 重建表（schema 扩展为 OHLC）
        conn.execute("DROP TABLE IF EXISTS ah_premium")
        conn.execute("""
            CREATE TABLE ah_premium (
                trade_date TEXT PRIMARY KEY,
                open       REAL,
                close      REAL,
                high       REAL,
                low        REAL
            )
        """)

        df.to_sql("ah_premium", conn, if_exists="append", index=False)
        print(f"  写入 {len(df)} 行 → ah_premium ✅")

        # 验证
        cnt = conn.execute("SELECT COUNT(*) FROM ah_premium").fetchone()[0]
        sample = conn.execute(
            "SELECT trade_date, close FROM ah_premium ORDER BY trade_date DESC LIMIT 3"
        ).fetchall()
        print(f"  验证: 共 {cnt} 行 | 最新: {sample}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
