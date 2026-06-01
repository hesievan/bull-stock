#!/usr/bin/env python3
"""
update_sectors.py — 计算板块热度并输出 JSON 供前端使用

用法:
  python scripts/update_sectors.py [--date YYYY-MM-DD] [--db data/heat_index.db] [--out web/data/sectors.json]
"""
import sys, os, json, argparse, logging
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="计算板块热度JSON")
    parser.add_argument("--date", default=None, help="trade_date, 默认昨日")
    parser.add_argument("--db", default="data/heat_index.db")
    parser.add_argument("--out", default="web/data/sectors.json")
    args = parser.parse_args()

    from src.indicators.calculator import calculate_sector_heat

    trade_date = args.date or date.today().strftime("%Y-%m-%d")
    logger.info("Sector heat date=%s db=%s", trade_date, args.db)

    results = calculate_sector_heat(trade_date, args.db)
    if not results:
        logger.error("No sector results!")
        sys.exit(1)

    # 添加 trade_date 字段
    for r in results:
        r["_trade_date"] = trade_date

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    logger.info("Wrote %d sectors to %s", len(results), args.out)
    logger.info("TOP5: %s", ", ".join(f"{r['sector_name']}({r['composite_score']:.0f})" for r in results[:5]))


if __name__ == "__main__":
    main()
