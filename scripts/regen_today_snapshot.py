"""
只重写 web/data/index.json + detail.json（当日快照），用 11 指标引擎实时算。
故意不动 history.json（周频回测口径）和 indicator_history.json（明细曲线），
避免 save_results_v2 把日频点混入周频走势。

用法: python scripts/regen_today_snapshot.py [trade_date]
"""

import os
import sys
import json
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.indicators.heat_index_v2 import compute_index_v2
from src.output.json_writer import get_heat_level

WEB_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "data")
DB = os.environ.get(
    "HEAT_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "heat_index.db")
)


def _atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _round_score(v):
    if v is None:
        return None
    try:
        f = float(v)
        import math

        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 1)
    except (TypeError, ValueError):
        return None


def main():
    trade_date = sys.argv[1] if len(sys.argv) > 1 else "2026-08-11"
    res = compute_index_v2(trade_date=trade_date, db_path=DB)
    if res is None or res.get("composite_score") is None:
        print("ERROR: engine returned no composite for", trade_date)
        sys.exit(1)

    composite = res["composite_score"]
    regime = res.get("regime")

    # 顶部卡片的分数/等级/维度分 对齐到周频回测走势末点 (history.json)，
    # 保证与走势线口径一致；原始值与百分位仍取自引擎实时计算。
    dims = res["dimensions"]
    hist_path = os.path.join(WEB_DATA, "history.json")
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding="utf-8") as f:
                hist = json.load(f)
            he = next((h for h in reversed(hist) if h["trade_date"] == trade_date), None)
            if he is None and hist:
                he = hist[-1]
            if he is not None:
                composite = he["composite_score"]
                dims = he["dimensions"]
                if he.get("regime"):
                    regime = he["regime"]
                # 标签按对齐后的综合分重算, 避免分数/标签口径不一致
                if regime and composite is not None:
                    regime = dict(regime)
                    regime["label"] = (
                        "过热"
                        if composite >= 65
                        else "分歧"
                        if composite >= 45
                        else "修复"
                        if composite >= 30
                        else "冰点"
                    )
                print(f"  (卡片分数对齐走势线末点 {trade_date}: composite={composite}, level={he.get('level')})")
        except Exception as e:
            print("  history 对齐跳过:", e)

    index_data = {
        "trade_date": trade_date,
        "composite_score": _round_score(composite),
        "level": get_heat_level(composite) if composite is not None else "unknown",
        "regime": regime,
        "dimensions": dims,
        "indicators_v2": {
            k: res.get("indicator_raw", {}).get(k) for k in res["indicators"] if k not in ("qvix", "qvix_components")
        },
        "qvix_display": res["indicators"].get("qvix"),
        "qvix_components": res["indicators"].get("qvix_components"),
        "version": "v2",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 补充展示指标（涨跌家数比/涨停占比等，仅供前端展示，不参与计算）
    try:
        conn = sqlite3.connect(DB)
        r = conn.execute("SELECT up_down_ratio FROM daily_updown WHERE trade_date=?", (trade_date,)).fetchone()
        if r:
            index_data["display_up_down_ratio"] = round(r[0], 4)
        r = conn.execute(
            "SELECT limit_up_ratio, limit_ratio FROM daily_limit WHERE trade_date=?", (trade_date,)
        ).fetchone()
        if r:
            index_data["display_limit_up_ratio"] = round(r[0], 4)
            index_data["display_limit_ratio"] = round(r[1], 4)
        r = conn.execute("SELECT below_net_rate FROM daily_below_net WHERE trade_date=?", (trade_date,)).fetchone()
        if r:
            index_data["display_below_net_rate"] = round(r[0], 4)
        conn.close()
    except Exception as e:
        print("display metrics skipped:", e)

    _atomic_write_json(os.path.join(WEB_DATA, "index.json"), index_data)
    detail_data = {**index_data, "indicators": res["indicators"]}
    _atomic_write_json(os.path.join(WEB_DATA, "detail.json"), detail_data)

    dims = {k: v["score"] for k, v in res["dimensions"].items()}
    print(f"✓ 已重写 index.json/detail.json ({trade_date})")
    print(f"  composite = {_round_score(composite)}  level = {index_data['level']}")
    print(f"  dimensions = {dims}")
    print("  fund 4 子项 / sentiment / structure 新增子项 = {")
    ind = res["indicators"]
    for k in ["margin_ratio_v2", "yield_spread", "m1_m2_spread", "southbound", "futures_discount", "breadth"]:
        print(f"    {k}: {ind.get(k)}")
    print("  }}")


if __name__ == "__main__":
    main()
