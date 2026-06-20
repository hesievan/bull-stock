#!/usr/bin/env python3
"""
快速回测验证 — 对比最近N天的计算结果是否一致

用途:
  1. CI 部署前验证指标计算正确性
  2. 本地开发后回归测试

用法:
  python scripts/validate_backtest.py           # 默认验证最近7天
  python scripts/validate_backtest.py --days 30  # 验证最近30天
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from src.indicators.calculator import calculate_heat_index
import logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

WEB_DATA = Path(__file__).parent.parent / "web" / "data"
HISTORY_FILE = WEB_DATA / "history.json"
TOLERANCE = 1.0  # 允许的最大分数偏差


def load_history():
    if not HISTORY_FILE.exists():
        return []
    with open(HISTORY_FILE, encoding="utf-8") as f:
        return json.load(f)


def validate_recent(days=7):
    """验证最近N天的计算结果与历史记录一致"""
    history = load_history()
    if not history:
        print("SKIP: No history.json found")
        return True

    recent = history[-days:]
    errors = []
    passed = 0

    for entry in recent:
        td = entry.get("trade_date")
        old_score = entry.get("composite_score")
        if not td or old_score is None:
            continue

        try:
            result = calculate_heat_index(trade_date=td)
            new_score = result.get("composite_score")
            if new_score is None:
                errors.append(f"{td}: new score is None")
                continue

            diff = abs(new_score - old_score)
            if diff > TOLERANCE:
                errors.append(f"{td}: {old_score:.1f} -> {new_score:.1f} (diff={diff:.1f})")
            else:
                passed += 1
        except Exception as e:
            errors.append(f"{td}: EXCEPTION - {str(e)[:60]}")

    print(f"Validated {len(recent)} days: {passed} passed, {len(errors)} failed")
    for err in errors:
        print(f"  FAIL: {err}")

    return len(errors) == 0


if __name__ == "__main__":
    days = 7
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        days = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 7

    ok = validate_recent(days)
    sys.exit(0 if ok else 1)
