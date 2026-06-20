#!/usr/bin/env python3
"""
修复 daily_turnover 预计算表

问题: 部分记录错误地使用了 AVG(turnover_rate) 而非 SUM(amount)/SUM(circ_mv)*10
正确公式: turnover_rate = SUM(amount) / SUM(circ_mv) * 10

用法:
  python scripts/fix_turnover.py          # 检查并修复
  python scripts/fix_turnover.py --check  # 仅检查，不修复
"""
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import DB_PATH, get_conn

def check_turnover():
    """检查 daily_turnover 表中的错误记录"""
    with get_conn() as conn:
        bad_dates = conn.execute("""
            SELECT sd.trade_date, 
                   AVG(sd.turnover_rate) as avg_tr,
                   SUM(sd.amount) / SUM(sd.circ_mv) * 10 as correct_tr,
                   dt.turnover_rate as current
            FROM stock_daily sd
            JOIN daily_turnover dt ON sd.trade_date = dt.trade_date
            WHERE sd.turnover_rate IS NOT NULL AND sd.amount > 0 AND sd.circ_mv > 0
            GROUP BY sd.trade_date
            HAVING ABS(dt.turnover_rate - (SUM(sd.amount) / SUM(sd.circ_mv) * 10)) > 0.1
            ORDER BY sd.trade_date
        """).fetchall()
        return bad_dates

def fix_turnover(dry_run=False):
    """修正 daily_turnover 表中的错误记录"""
    bad_dates = check_turnover()
    
    if not bad_dates:
        print("✅ daily_turnover 表数据正确，无需修复")
        return 0

    print(f"⚠️ 发现 {len(bad_dates)} 条错误记录:")
    for row in bad_dates[:10]:  # 只显示前10条
        print(f"  {row[0]}: 当前={row[3]:.4f}, 正确={row[2]:.4f}, 差异={abs(row[3]-row[2]):.4f}")
    if len(bad_dates) > 10:
        print(f"  ... 还有 {len(bad_dates)-10} 条")

    if dry_run:
        print("\n(dry-run 模式，未修改)")
        return len(bad_dates)

    # 修正记录
    with get_conn() as conn:
        fixed = 0
        for row in bad_dates:
            trade_date = row[0]
            correct_value = row[2]
            conn.execute(
                "UPDATE daily_turnover SET turnover_rate = ? WHERE trade_date = ?",
                (correct_value, trade_date)
            )
            fixed += 1

    print(f"\n✅ 已修正 {fixed} 条记录")
    return fixed

if __name__ == "__main__":
    dry_run = "--check" in sys.argv
    fix_turnover(dry_run=dry_run)
