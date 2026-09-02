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

  --since 指定起始年份, 实际会多拉一年(差分与序列级口径判定需要前序季度),
  多出的那一年写入是幂等的。

口径:
  表内 gdp 字段统一存**当季值**(单季), 因为巴菲特指标按"同年 4 个季度之和"算年度 GDP。
  tushare cn_gdp 的 gdp 字段实为**年初至今累计值**(YTD, 实测 2025Q2=659861.6
  = 2025Q1 318466.4 + 当季 341395.2), 早期入库路径做过差分所以库内历史是当季值;
  本脚本此前盲写源值, 导致 2026Q2=695704.0(累计) 入库, 会把 2026 年度 GDP 抬高约 80%。
  现统一走 normalize_to_quarterly(): 判定累计口径后差分回当季值(361511.1)再入库,
  原始累计值保留在 gdp_accumulate 列备查。
"""

import sys
import os
import re
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from src.config import load_dotenv_safe

load_dotenv_safe()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fetch_gdp.log"),
            mode="a",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


# 非 Q1 季度: 源值超过"上一年同季度当季值"或"同年上一季度源值"的该倍数即判为累计值
# (当季序列这两个比值约 1.0~1.1; 累计序列 Q2/Q3 约 2.0~3.0)
_CUMULATIVE_RATIO = 1.5
# 序列级判定阈值: 累计值每年 Q1 重置, 故 累计Q1/上年累计Q4 ≈ 0.24; 当季序列 ≈ 0.82
_SERIES_CUM_MAX_RATIO = 0.5
_SERIES_QUARTERLY_MIN_RATIO = 0.6
# 差分结果与"上年同季度当季值"的合理偏离带, 越界视为异常(宁可丢弃不写错量级)
_SANITY_LO, _SANITY_HI = 0.5, 2.0

_Q_RE = re.compile(r"^(\d{4})\D*(\d{1,2})")


def parse_quarter(q: str) -> tuple[int, int]:
    """'2026Q2' / '202602' / '2026-06' -> (2026, 2); 无法解析 -> (0, 0)。

    年份后的数字: 1~4 视为季度号(含 01~04 补零写法), 5~12 视为月份(6->Q2)。
    """
    m = _Q_RE.match(str(q).strip())
    if not m:
        return (0, 0)
    year = int(m.group(1))
    n = int(m.group(2))
    if 1 <= n <= 4:
        return (year, n)
    if 5 <= n <= 12:
        return (year, (n + 2) // 3)
    return (year, 0)


def detect_cumulative_series(raw: dict[str, float]) -> bool | None:
    """判定整批 GDP 序列是累计值(YTD)还是当季值; 数据不足时返回 None。

    累计值每年 Q1 重置: 累计Q1 / 上年累计Q4 ≈ 0.24 (实测 2026Q1/2025Q4 = 0.238)
    当季值序列:         当季Q1 / 上年当季Q4 ≈ 0.82 (实测 2025Q1/2024Q4 = 0.821)
    两者差距极大, 用中位数即可稳定区分, 且能覆盖逐行比值判不出来的 Q4。
    """
    ratios = []
    for key, val in raw.items():
        year, q = parse_quarter(key)
        if q != 1 or year == 0 or not val:
            continue
        prev_q4 = raw.get(f"{year - 1}Q4")
        if prev_q4:
            ratios.append(val / prev_q4)
    if not ratios:
        return None
    ratios.sort()
    median = ratios[len(ratios) // 2]
    if median < _SERIES_CUM_MAX_RATIO:
        return True
    if median > _SERIES_QUARTERLY_MIN_RATIO:
        return False
    return None


def normalize_to_quarterly(records: list[dict], known: dict[str, float] | None = None) -> list[dict]:
    """把 gdp 字段统一归一化为**当季值**(单季), 并在 gdp_accumulate 保留源累计值。

    背景: tushare cn_gdp 的 gdp 字段实际是**年初至今累计值**(YTD), 例如
    2025Q2=659861.6 = Q1 318466.4 + 当季 341395.2。早期入库路径做过差分,
    所以库内 2024/2025 是当季值; 而本脚本此前盲写源值, 导致 2026Q2=695704.0
    (累计) 直接入库, 会把巴菲特指标的年度 GDP 分母抬高约 80%。

    累计判定(整批一个口径, 三级优先):
      0) **序列级**: 累计值每年 Q1 重置, 故 累计Q1/上年累计Q4≈0.24, 而当季序列≈0.82。
         用所有 (Y,Q1)/(Y-1,Q4) 比值的中位数判整批口径 —— 这是唯一能覆盖 Q4 的判定
         (Q4 的 累计Q4/累计Q3≈1.38, 与当季序列的 1.10 太近, 逐行比值判不出来)。
      1) 序列级无法判定时(批次太小), 退回逐行: 源值 > 1.5 x 上年同季度当季值。
      2) 再退: 源值 > 1.5 x 同年上一季度源值。
    差分: 当季 = 源累计(q) - 源累计(q-1); 结果再与上年同季度做 0.5~2.0 倍合理性校验,
    缺前序季度或越界时宁可丢弃也不写错量级。
    """
    known = dict(known or {})
    # 本批源值(未差分), 供累计差分与序列级判定使用
    raw = {str(r["quarter"]): float(r["gdp"]) for r in records if r.get("gdp") is not None}
    series_is_cum = detect_cumulative_series(raw)
    if series_is_cum is None:
        logger.warning("GDP: 批量过小, 无法做序列级口径判定, 退回逐行判定")
    else:
        logger.info("GDP: 序列口径判定 = %s", "累计值(YTD)" if series_is_cum else "当季值")
    out: list[dict] = []

    for rec in sorted(records, key=lambda r: str(r.get("quarter"))):
        quarter = str(rec.get("quarter"))
        year, q = parse_quarter(quarter)
        gdp = rec.get("gdp")
        acc = rec.get("gdp_accumulate")
        item = {
            "quarter": quarter,
            "gdp": gdp,
            "gdp_yoy": rec.get("gdp_yoy"),
            "gdp_accumulate": acc,
            "gdp_accumulate_yoy": rec.get("gdp_accumulate_yoy"),
        }

        if gdp is None or q == 0:
            if q == 0:
                logger.warning("GDP %s: 季度格式无法解析, 原样保留", quarter)
            out.append(item)
            continue

        # Q1: 累计值 == 当季值, 无需判定
        if q == 1:
            known[quarter] = float(gdp)
            out.append(item)
            continue

        prev_raw = raw.get(f"{year}Q{q - 1}")
        base = known.get(f"{year - 1}Q{q}")
        if series_is_cum is not None:
            is_cum = series_is_cum
        elif base:
            is_cum = float(gdp) > _CUMULATIVE_RATIO * float(base)
        elif prev_raw is not None:
            is_cum = float(gdp) > _CUMULATIVE_RATIO * float(prev_raw)
        else:
            is_cum = False

        if not is_cum:
            known[quarter] = float(gdp)
            out.append(item)
            continue

        if prev_raw is None:
            logger.warning(
                "GDP %s: 判定为累计值 (%.1f, 上年同季度 %.1f) 但同年 Q%d 源值缺失, 无法差分, 丢弃该行",
                quarter,
                float(gdp),
                float(base or 0),
                q - 1,
            )
            continue

        fixed = float(gdp) - float(prev_raw)
        if base and not (_SANITY_LO * base <= fixed <= _SANITY_HI * base):
            logger.warning(
                "GDP %s: 差分结果 %.1f 相对上年同季度 %.1f 偏离 %.2f 倍, 超出 [%.1f, %.1f], 丢弃该行",
                quarter,
                fixed,
                float(base),
                fixed / float(base),
                _SANITY_LO,
                _SANITY_HI,
            )
            continue
        logger.warning(
            "GDP %s: 源口径为累计值 (%.1f), 差分回当季值 %.1f (= %.1f - %.1f)",
            quarter,
            float(gdp),
            fixed,
            float(gdp),
            float(prev_raw),
        )
        item["gdp"] = fixed
        if acc is None:
            item["gdp_accumulate"] = float(gdp)
        out.append(item)
        known[quarter] = fixed

    return out


def fetch_gdp(since: str = None):
    """从 Tushare 拉取 GDP 季度数据并写入 gdp_quarterly"""
    import tushare as ts
    import pandas as pd
    from src.data.database import DB_PATH, init_database, get_conn

    if not os.environ.get("TUSHARE_TOKEN"):
        logger.error("TUSHARE_TOKEN 未设置")
        return False

    init_database()
    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])

    # 多拉一年: 保证批次内至少有一组 (Y,Q1)/(Y-1,Q4), 序列级口径判定才有依据,
    # 且 since 年各季度的前序季度源值齐全(差分需要)。多写的那一年是幂等的。
    start_year = int(since) - 1 if since else 2000
    try:
        start_q = f"{max(2000, start_year)}0101"
        df = pro.cn_gdp(start_q=start_q)
    except Exception as e:
        logger.error("Failed to fetch GDP: %s", str(e)[:80])
        return False

    if df is None or df.empty:
        logger.warning("No GDP data returned")
        return False

    df["gdp"] = pd.to_numeric(df.get("gdp"), errors="coerce")
    df["gdp_yoy"] = pd.to_numeric(df.get("gdp_yoy"), errors="coerce")
    if "gdp_accumulate" in df.columns:
        df["gdp_accumulate"] = pd.to_numeric(df.get("gdp_accumulate"), errors="coerce")
        df["gdp_accumulate_yoy"] = pd.to_numeric(df.get("gdp_accumulate_yoy"), errors="coerce")

    records = []
    for _, row in df.iterrows():
        records.append(
            {
                "quarter": str(row["quarter"]),
                "gdp": None if pd.isna(row.get("gdp")) else float(row["gdp"]),
                "gdp_yoy": None if pd.isna(row.get("gdp_yoy")) else float(row["gdp_yoy"]),
                "gdp_accumulate": (
                    None if pd.isna(row.get("gdp_accumulate")) else float(row["gdp_accumulate"])
                ),
                "gdp_accumulate_yoy": (
                    None if pd.isna(row.get("gdp_accumulate_yoy")) else float(row["gdp_accumulate_yoy"])
                ),
            }
        )

    # 已入库的当季值, 供累计值差分时补齐同年更早的季度
    with get_conn(DB_PATH) as conn:
        known = {r[0]: r[1] for r in conn.execute("SELECT quarter, gdp FROM gdp_quarterly WHERE gdp IS NOT NULL")}

    normalized = normalize_to_quarterly(records, known=known)

    with get_conn(DB_PATH) as conn:
        written = 0
        for item in normalized:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO gdp_quarterly "
                    "(quarter, gdp, gdp_yoy, gdp_accumulate, gdp_accumulate_yoy) VALUES (?, ?, ?, ?, ?)",
                    (
                        item["quarter"],
                        item["gdp"],
                        item["gdp_yoy"],
                        item["gdp_accumulate"],
                        item["gdp_accumulate_yoy"],
                    ),
                )
                written += 1
            except Exception as e:
                logger.warning("Failed to insert row %s: %s", item["quarter"], str(e)[:40])
        conn.commit()

    dropped = len(records) - len(normalized)
    logger.info("GDP data saved: %d rows (dropped %d ambiguous cumulative rows)", written, dropped)
    return True


if __name__ == "__main__":
    since = None
    if "--since" in sys.argv:
        idx = sys.argv.index("--since")
        if idx + 1 < len(sys.argv):
            since = sys.argv[idx + 1]
    fetch_gdp(since=since)
