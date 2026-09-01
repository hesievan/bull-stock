"""
显示 V2 引擎实时计算值。

打印结构:
  - 11 个指标: 原始值(raw) + 百分位得分(score 0~100) + 权重 + 属于维度
  - QVIX 恐慌指数(仅展示不计分)
  - 4 个维度加权得分
  - 综合得分 + 档位

用法: python scripts/show_engine_live.py [trade_date]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.indicators.heat_index_v2 import (
    compute_index_v2,
    INDICATOR_WEIGHTS,
    INDICATOR_DIMENSIONS,
    DIMENSIONS,
)
from src.output.json_writer import get_heat_level
from src.data.database import DB_PATH

# 指标中文名 + 原始值格式化
IND_META = {
    "pe": ("大盘PE", lambda v: f"{v:.2f}" if v is not None else "—"),
    "buffett": ("巴菲特指标(总市值/GDP)", lambda v: f"{v:.2%}" if v is not None else "—"),
    "margin_ratio": ("两融余额市值比", lambda v: f"{v:.4%}" if v is not None else "—"),
    "yield_spread": ("国债期限利差(10Y-2Y)", lambda v: f"{v:.4f}" if v is not None else "—"),
    "m1_m2_spread": ("M1-M2剪刀差", lambda v: f"{v:.2f}%" if v is not None else "—"),
    "southbound": ("南向净买额(亿元)", lambda v: f"{v:.2f}" if v is not None else "—"),
    "seal_rate": ("涨停封板率", lambda v: f"{v:.2%}" if v is not None else "—"),
    "turnover_m2": ("成交额M2比", lambda v: f"{v:.4%}" if v is not None else "—"),
    "turnover": (
        "换手率",
        lambda v: f"{v:.2f}%" if v is not None else "—",
    ),  # raw 已是百分比数值(2.33 表示 2.33%), 与 app.html 一致
    "futures_discount": ("IF基差率", lambda v: f"{v:.4%}" if v is not None else "—"),
    "new_high": ("创新高占比", lambda v: f"{v:.2%}" if v is not None else "—"),
    "ma_alignment": ("MA排列比", lambda v: f"{v:.2%}" if v is not None else "—"),
    "breadth": ("涨跌家数广度", lambda v: f"{v:.3f}" if v is not None else "—"),
}
DIM_LABEL = {"valuation": "估值", "fund": "资金", "sentiment": "情绪", "structure": "结构"}


def main():
    trade_date = sys.argv[1] if len(sys.argv) > 1 else "2026-08-11"
    res = compute_index_v2(trade_date=trade_date, db_path=DB_PATH)
    if res is None or res.get("composite_score") is None:
        print("ERROR: 引擎未返回有效综合得分", trade_date)
        sys.exit(1)

    raw = res["indicator_raw"]
    scores = res["indicators"]
    print("=" * 78)
    print(f"  牛市热度指数 V2 引擎实时值  |  交易日: {res['trade_date']}")
    print(f"  数据更新时间: {res['updated_at']}")
    print("=" * 78)

    # 指标明细
    # 引擎 res["indicators"] 里两融键名为 margin_ratio_v2, 其余键名与内部一致
    IND_KEY_MAP = {k: ("margin_ratio_v2" if k == "margin_ratio" else k) for k in INDICATOR_WEIGHTS}
    print(f"{'指标':<22}{'原始值':<14}{'得分':<8}{'权重':<8}{'维度'}")
    print("-" * 78)
    contrib = {}
    for k in INDICATOR_WEIGHTS:
        name, fmt = IND_META[k]
        rv = raw.get(k)
        sc = scores.get(IND_KEY_MAP[k])
        w = INDICATOR_WEIGHTS[k]
        score_str = f"{sc:.1f}" if sc is not None else "  — "
        w_str = f"{w:.0%}"
        dim = DIM_LABEL[INDICATOR_DIMENSIONS[k]]
        print(f"{name:<20}{fmt(rv):<16}{score_str:<10}{w_str:<10}{dim}")
        if sc is not None:
            contrib[k] = sc * w

    # QVIX
    qvix = scores.get("qvix")
    qc = scores.get("qvix_components")
    print("-" * 78)
    print(f"{'QVIX恐慌指数':<20}{(f'{qvix:.2f}' if qvix is not None else '—'):<16}{'(仅展示不计分)':<12}")
    if qc:
        for ck, cv in qc.items():
            print(f"   └ {ck:<18}: {cv:.2f}" if cv is not None else f"   └ {ck}: —")

    # 维度得分
    print("=" * 78)
    print("  维度得分 (按指标权重加权)")
    print("-" * 78)
    for d in DIMENSIONS:
        ds = res["dimensions"][d]
        sc = ds["score"]
        print(f"  {DIM_LABEL[d]:<8}: {sc:.1f}" if sc is not None else f"  {DIM_LABEL[d]:<8}: —")

    # 综合
    comp = res["composite_score"]
    print("=" * 78)
    print(f"  综合热度得分: {comp:.1f}   档位: {get_heat_level(comp)}")
    print("=" * 78)

    # 各维度贡献拆解
    print("\n  综合得分贡献拆解 (得分×权重, 取 Top5):")
    for k, c in sorted(contrib.items(), key=lambda x: -x[1])[:5]:
        print(f"    {IND_META[k][0]:<22} +{c:.2f}")
    print(f"    {'—' * 30}")
    print(f"    合计 ≈ {sum(contrib.values()):.1f}")


if __name__ == "__main__":
    main()
