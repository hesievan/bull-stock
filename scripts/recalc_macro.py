#!/usr/bin/env python3
"""
重新计算历史数据的宏观维度

用法:
  python scripts/recalc_macro.py              # 计算所有缺失macro的日期
  python scripts/recalc_macro.py --start 2015-01-01 --end 2020-01-01  # 指定范围
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.indicators.calculator import calculate_heat_index

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "data")


def recalc_macro(start=None, end=None):
    history_file = os.path.join(DATA_DIR, "history.json")
    with open(history_file, encoding="utf-8") as f:
        history = json.load(f)

    # 找出需要重新计算的日期
    to_calc = []
    for item in history:
        dims = item.get("dimensions", {})
        has_macro = "macro" in dims
        if not has_macro:
            td = item["trade_date"]
            if start and td < start:
                continue
            if end and td > end:
                continue
            to_calc.append(item)

    print(f"需要重新计算: {len(to_calc)} 条")
    print(f"预计耗时: {len(to_calc) * 2 / 60:.1f} 分钟")
    print("-" * 60)

    success = 0
    failed = 0
    t_start = time.time()

    for i, item in enumerate(to_calc):
        td = item["trade_date"]
        try:
            result = calculate_heat_index(trade_date=td)
            if result and result.get("dim_macro") is not None:
                # 更新dimensions
                if "dimensions" not in item:
                    item["dimensions"] = {}
                item["dimensions"]["macro"] = result["dim_macro"]
                success += 1
            else:
                # macro计算失败，设置为None
                if "dimensions" not in item:
                    item["dimensions"] = {}
                item["dimensions"]["macro"] = None
                failed += 1
        except Exception:
            failed += 1
            if "dimensions" not in item:
                item["dimensions"] = {}
            item["dimensions"]["macro"] = None

        # 进度输出
        if (i + 1) % 50 == 0:
            elapsed = time.time() - t_start
            rate = (i + 1) / elapsed
            remaining = (len(to_calc) - i - 1) / rate
            print(f"进度: {i+1}/{len(to_calc)} ({success}成功, {failed}失败) "
                  f"[{elapsed:.0f}s, 剩余{remaining:.0f}s]")

    # 保存结果
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - t_start
    print("-" * 60)
    print(f"完成! 成功: {success}, 失败: {failed}, 耗时: {elapsed:.0f}s")
    print(f"已保存到: {history_file}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Recalculate macro dimension for history")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    recalc_macro(start=args.start, end=args.end)


if __name__ == "__main__":
    main()
