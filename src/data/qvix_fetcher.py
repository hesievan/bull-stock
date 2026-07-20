"""
QVIX 恐慌指数获取模块

从 optbbs.com 获取 CFFEX 股指期权 QVIX（加权隐含波动率）数据，
计算 A 股恐慌指数 = 0.3 × 上证50股指隐波 + 0.4 × 沪深300股指隐波 + 0.3 × 中证1000股指隐波

方案 A（纯 CFFEX 股指期权）:
  - 上证50股指期权 HO (2023-01 起)
  - 沪深300股指期权 IO (2019-12 起)
  - 中证1000股指期权 MO (2022-07 起)
"""
import logging
import io
import urllib.request
import pandas as pd

logger = logging.getLogger(__name__)

OPTBBS_URL = "http://1.optbbs.com/d/csv/d/k.csv"

# CFFEX index option QVIX column indices in the optbbs CSV:
# Columns: [date, open, high, low, close]
QVIX_COLUMNS = {
    "50index":  [0, 79, 80, 81, 82],    # 上证50股指期权 HO
    "300index": [0, 17, 18, 19, 20],    # 沪深300股指期权 IO
    "1000index":[0, 25, 26, 27, 28],    # 中证1000股指期权 MO
}

# optbbs CSV 当前列数；上游改结构时显式报错而非静默错配数据
EXPECTED_QVIX_COLUMNS = 83

# 恐慌指数权重
PANIC_WEIGHTS = {
    "50index": 0.3,
    "300index": 0.4,
    "1000index": 0.3,
}


def _download_csv(timeout: int = 60) -> pd.DataFrame:
    """下载 optbbs.com 的 QVIX CSV 数据"""
    req = urllib.request.Request(
        OPTBBS_URL,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    )
    data = urllib.request.urlopen(req, timeout=timeout).read()
    df = pd.read_csv(io.BytesIO(data), encoding="gbk")
    logger.info("QVIX CSV 下载完成: %d 行, %d 列", len(df), len(df.columns))
    return df


def _extract_qvix(df_raw: pd.DataFrame, cols: list[int]) -> pd.Series:
    """从原始 CSV 中提取单个品种的 QVIX close 序列"""
    if len(df_raw.columns) < max(cols) + 1:
        raise ValueError(
            f"QVIX CSV 列数异常({len(df_raw.columns)})，期望≥{max(cols) + 1}，"
            f"上游结构可能已变更，停止按硬编码列索引提取"
        )
    s = df_raw.iloc[:, cols].copy()
    s.columns = ["date", "open", "high", "low", "close"]
    s["date"] = pd.to_datetime(s["date"], errors="coerce")
    s["close"] = pd.to_numeric(s["close"], errors="coerce")
    s = s[(s["close"] > 0) & (s["close"] < 200)].dropna(subset=["close"])
    s = s.sort_values("date").set_index("date")
    return s["close"]


def fetch_qvix_data(timeout: int = 60) -> pd.DataFrame:
    """获取所有 CFFEX 品种的 QVIX 数据，合并为一个 DataFrame

    Returns:
        DataFrame with columns: 50index, 300index, 1000index  (index=date)
    """
    df_raw = _download_csv(timeout=timeout)
    series = {}
    for name, cols in QVIX_COLUMNS.items():
        series[name] = _extract_qvix(df_raw, cols)
    merged = pd.DataFrame(series).sort_index()
    logger.info("QVIX 合并完成: %d 天, 范围 %s ~ %s",
                len(merged), merged.index.min().date(), merged.index.max().date())
    return merged


def compute_panic_index(qvix_df: pd.DataFrame) -> pd.DataFrame:
    """计算恐慌指数及其成分

    Args:
        qvix_df: DataFrame with columns 50index, 300index, 1000index

    Returns:
        DataFrame with columns:
            qvix_50, qvix_300, qvix_1000,
            panic_index (加权合成),
            concentration (1000 - 50, 恐慌集中度)
    """
    df = qvix_df.copy()
    df["panic_index"] = (
        df["50index"] * PANIC_WEIGHTS["50index"]
        + df["300index"] * PANIC_WEIGHTS["300index"]
        + df["1000index"] * PANIC_WEIGHTS["1000index"]
    )
    df["concentration"] = df["1000index"] - df["50index"]
    # 重命名以保持列名一致
    df = df.rename(columns={
        "50index": "qvix_50",
        "300index": "qvix_300",
        "1000index": "qvix_1000",
    })
    return df


def fetch_panic_index(timeout: int = 60) -> pd.DataFrame:
    """一站式获取恐慌指数（下载 + 计算）

    Returns:
        DataFrame with: qvix_50, qvix_300, qvix_1000, panic_index, concentration
    """
    raw = fetch_qvix_data(timeout=timeout)
    return compute_panic_index(raw)
