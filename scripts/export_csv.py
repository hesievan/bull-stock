#!/usr/bin/env python3
"""
数据导出工具 — 导出热度指数历史数据为CSV

用法:
  python scripts/export_csv.py                    # 导出全部历史
  python scripts/export_csv.py --days 30          # 导出最近30天
  python scripts/export_csv.py --start 2024-01-01 # 从指定日期开始
  python scripts/export_csv.py --end 2026-06-17   # 到指定日期结束
  python scripts/export_csv.py --output report.csv # 指定输出文件
"""
import sys
import os
import json
import csv
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "data")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")

DIM_KEYS = ["valuation", "fund", "sentiment", "technical", "structure"]
DIM_LABELS = {
    "valuation": "估值",
    "fund": "资金",
    "sentiment": "情绪",
    "technical": "技术",
    "structure": "结构",
}


def load_history():
    path = os.path.join(DATA_DIR, "history.json")
    if not os.path.exists(path):
        print("ERROR: history.json not found")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def filter_data(history, days=None, start=None, end=None):
    data = history
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        data = [h for h in data if h.get("trade_date", "") >= cutoff]
    if start:
        data = [h for h in data if h.get("trade_date", "") >= start]
    if end:
        data = [h for h in data if h.get("trade_date", "") <= end]
    return data


def export_csv(data, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    headers = ["日期", "综合热度", "热度等级"]
    for key in DIM_KEYS:
        headers.append(DIM_LABELS[key])
    headers.append("较昨日变化")

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        prev_score = None
        for h in data:
            row = [
                h.get("trade_date", ""),
                h.get("composite_score", ""),
                h.get("level", ""),
            ]
            dims = h.get("dimensions", {})
            for key in DIM_KEYS:
                row.append(dims.get(key, {}).get("score", ""))
            change = ""
            score = h.get("composite_score")
            if score is not None and prev_score is not None:
                change = round(score - prev_score, 1)
            row.append(change)
            writer.writerow(row)
            prev_score = score

    print(f"Exported {len(data)} rows to {output_path}")
    return output_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Export heat index data to CSV")
    parser.add_argument("--days", type=int, help="Export last N days")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", help="Output file path")
    args = parser.parse_args()

    history = load_history()
    data = filter_data(history, days=args.days, start=args.start, end=args.end)

    if not data:
        print("No data to export")
        sys.exit(1)

    if args.output:
        output = args.output
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = os.path.join(REPORT_DIR, f"heat_index_{ts}.csv")

    export_csv(data, output)


if __name__ == "__main__":
    main()
