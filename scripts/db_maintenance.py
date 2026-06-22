#!/usr/bin/env python3
"""
数据库压缩与维护

用法:
  python scripts/db_maintenance.py              # 检查数据库状态
  python scripts/db_maintenance.py --vacuum     # 压缩数据库
  python scripts/db_maintenance.py --archive 2020  # 归档指定年份之前的数据
"""
import sys
import os
import sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import DB_PATH, get_conn
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def check_db_status(db_path=None):
    """检查数据库状态"""
    path = db_path or DB_PATH
    if not os.path.exists(path):
        print(f"Database not found: {path}")
        return

    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"Database: {path}")
    print(f"Size: {size_mb:.1f} MB")

    with get_conn(path) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        print(f"\nTables ({len(tables)}):")
        for (tname,) in tables:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()[0]
                print(f"  {tname}: {count:,} rows")
            except Exception:
                print(f"  {tname}: (error)")

        # WAL status
        wal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        print(f"\nJournal mode: {wal}")


def vacuum_db(db_path=None):
    """压缩数据库"""
    path = path = db_path or DB_PATH
    size_before = os.path.getsize(path) / (1024 * 1024)
    logger.info("Vacuuming %s (%.1f MB)...", path, size_before)

    conn = sqlite3.connect(path)
    conn.execute("VACUUM")
    conn.close()

    size_after = os.path.getsize(path) / (1024 * 1024)
    logger.info("Done: %.1f MB -> %.1f MB (%.1f%% reduction)",
                size_before, size_after, (1 - size_after/size_before) * 100)


def archive_before_year(year: int, db_path=None):
    """归档指定年份之前的数据到独立文件"""
    path = db_path or DB_PATH
    cutoff = f"{year}-01-01"
    archive_path = path.replace(".db", f"_archive_{year}.db")

    logger.info("Archiving data before %s to %s", cutoff, archive_path)

    with get_conn(path) as conn:
        # 统计待归档行数
        tables_to_archive = ["stock_daily", "index_daily", "margin_history",
                             "northbound_history", "bond_yield"]
        total = 0
        for tname in tables_to_archive:
            try:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {tname} WHERE trade_date < ?", (cutoff,)
                ).fetchone()[0]
                total += count
            except Exception:
                pass

        if total == 0:
            logger.info("No data to archive")
            return

        logger.info("Found %d rows to archive", total)

        # 创建归档数据库
        archive_conn = sqlite3.connect(archive_path)
        archive_conn.execute("PRAGMA journal_mode=WAL")

        for tname in tables_to_archive:
            try:
                # 复制表结构
                conn.execute(f"SELECT * FROM {tname} WHERE 1=0").fetchall()
                cols = [d[1] for d in conn.execute(f"PRAGMA table_info({tname})").fetchall()]

                # 导出
                rows = conn.execute(
                    f"SELECT * FROM {tname} WHERE trade_date < ?", (cutoff,)
                ).fetchall()
                if rows:
                    placeholders = ",".join(["?"] * len(cols))
                    archive_conn.execute(
                        f"CREATE TABLE IF NOT EXISTS {tname} AS SELECT * FROM {tname} WHERE 1=0"
                    )
                    archive_conn.executemany(
                        f"INSERT INTO {tname} VALUES ({placeholders})", rows
                    )
                    logger.info("  Archived %s: %d rows", tname, len(rows))
            except Exception as e:
                logger.warning("  Skip %s: %s", tname, str(e)[:60])

        archive_conn.commit()
        archive_conn.close()

        # 删除原表中已归档数据
        for tname in tables_to_archive:
            try:
                deleted = conn.execute(
                    f"DELETE FROM {tname} WHERE trade_date < ?", (cutoff,)
                ).rowcount
                if deleted:
                    logger.info("  Deleted from %s: %d rows", tname, deleted)
            except Exception:
                pass

    logger.info("Archive complete: %s", archive_path)


if __name__ == "__main__":
    if "--vacuum" in sys.argv:
        vacuum_db()
    elif "--archive" in sys.argv:
        idx = sys.argv.index("--archive")
        year = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 2020
        archive_before_year(year)
    else:
        check_db_status()
