#!/usr/bin/env python3
"""
数据库综合工具 — 状态检查、压缩、备份、归档

用法:
  python scripts/db_tools.py status                    # 检查数据库状态
  python scripts/db_tools.py vacuum                    # VACUUM 压缩数据库
  python scripts/db_tools.py archive <year>            # 归档指定年份之前的数据
  python scripts/db_tools.py compress                  # gzip 压缩数据库文件
  python scripts/db_tools.py decompress                # 解压 gzip 数据库文件
  python scripts/db_tools.py size                      # 显示数据库和压缩文件大小
  python scripts/db_tools.py backup                    # 创建带日期的备份
  python scripts/db_tools.py restore [backup_file]     # 从备份恢复（默认最新备份）
  python scripts/db_tools.py list                      # 列出所有备份
"""

import sys
import os
import gzip
import shutil
import glob
import sqlite3
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import DB_PATH, get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
GZ_PATH = os.path.join(DB_DIR, "heat_index.db.gz")
BACKUP_DIR = os.path.join(DB_DIR, "backups")


# ── 状态检查 ─────────────────────────────────────────────────────────────────


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
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        print(f"\nTables ({len(tables)}):")
        for (tname,) in tables:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()[0]
                print(f"  {tname}: {count:,} rows")
            except Exception:
                print(f"  {tname}: (error)")

        wal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        print(f"\nJournal mode: {wal}")


# ── VACUUM 压缩 ──────────────────────────────────────────────────────────────


def vacuum_db(db_path=None):
    """VACUUM 压缩数据库"""
    path = db_path or DB_PATH
    size_before = os.path.getsize(path) / (1024 * 1024)
    logger.info("Vacuuming %s (%.1f MB)...", path, size_before)

    conn = sqlite3.connect(path)
    conn.execute("VACUUM")
    conn.close()

    size_after = os.path.getsize(path) / (1024 * 1024)
    logger.info(
        "Done: %.1f MB -> %.1f MB (%.1f%% reduction)", size_before, size_after, (1 - size_after / size_before) * 100
    )


# ── 归档 ─────────────────────────────────────────────────────────────────────


def archive_before_year(year: int, db_path=None):
    """归档指定年份之前的数据到独立文件"""
    path = db_path or DB_PATH
    cutoff = f"{year}-01-01"
    archive_path = path.replace(".db", f"_archive_{year}.db")

    logger.info("Archiving data before %s to %s", cutoff, archive_path)

    with get_conn(path) as conn:
        tables_to_archive = ["stock_daily", "index_daily", "margin_history", "northbound_history", "bond_yield"]
        total = 0
        for tname in tables_to_archive:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {tname} WHERE trade_date < ?", (cutoff,)).fetchone()[0]
                total += count
            except Exception:
                pass

        if total == 0:
            logger.info("No data to archive")
            return

        logger.info("Found %d rows to archive", total)

        archive_conn = sqlite3.connect(archive_path)
        archive_conn.execute("PRAGMA journal_mode=WAL")

        for tname in tables_to_archive:
            try:
                conn.execute(f"SELECT * FROM {tname} WHERE 1=0").fetchall()
                cols = [d[1] for d in conn.execute(f"PRAGMA table_info({tname})").fetchall()]

                rows = conn.execute(f"SELECT * FROM {tname} WHERE trade_date < ?", (cutoff,)).fetchall()
                if rows:
                    archive_conn.execute(f"CREATE TABLE IF NOT EXISTS {tname} AS SELECT * FROM {tname} WHERE 1=0")
                    archive_conn.executemany(f"INSERT INTO {tname} VALUES ({','.join(['?'] * len(cols))})", rows)
                    logger.info("  Archived %s: %d rows", tname, len(rows))
            except Exception as e:
                logger.warning("  Skip %s: %s", tname, str(e)[:60])

        archive_conn.commit()
        archive_conn.close()

        for tname in tables_to_archive:
            try:
                deleted = conn.execute(f"DELETE FROM {tname} WHERE trade_date < ?", (cutoff,)).rowcount
                if deleted:
                    logger.info("  Deleted from %s: %d rows", tname, deleted)
            except Exception:
                pass

    logger.info("Archive complete: %s", archive_path)


# ── gzip 压缩/解压 ──────────────────────────────────────────────────────────


def compress():
    db_path = os.path.join(DB_DIR, "heat_index.db")
    if not os.path.exists(db_path):
        print(f"ERROR: {db_path} not found")
        sys.exit(1)
    before = os.path.getsize(db_path)
    with open(db_path, "rb") as f_in, gzip.open(GZ_PATH, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    after = os.path.getsize(GZ_PATH)
    ratio = (1 - after / before) * 100 if before else 0
    print(f"Compressed: {before:,} → {after:,} bytes ({ratio:.1f}% reduction)")
    return GZ_PATH


def decompress():
    db_path = os.path.join(DB_DIR, "heat_index.db")
    if not os.path.exists(GZ_PATH):
        print(f"ERROR: {GZ_PATH} not found")
        sys.exit(1)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with gzip.open(GZ_PATH, "rb") as f_in, open(db_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    size = os.path.getsize(db_path)
    print(f"Decompressed to {db_path} ({size:,} bytes)")
    return db_path


def show_size():
    db_path = os.path.join(DB_DIR, "heat_index.db")
    for label, path in [("DB", db_path), ("GZ", GZ_PATH)]:
        if os.path.exists(path):
            size = os.path.getsize(path)
            if size > 1024 * 1024 * 1024:
                print(f"{label}: {size / (1024**3):.2f} GB")
            elif size > 1024 * 1024:
                print(f"{label}: {size / (1024**2):.1f} MB")
            else:
                print(f"{label}: {size / 1024:.1f} KB")
        else:
            print(f"{label}: not found")


# ── 备份/恢复 ────────────────────────────────────────────────────────────────


def backup():
    db_path = os.path.join(DB_DIR, "heat_index.db")
    if not os.path.exists(db_path):
        print(f"ERROR: {db_path} not found")
        sys.exit(1)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"heat_index_{ts}.db.gz")
    before = os.path.getsize(db_path)
    with open(db_path, "rb") as f_in, gzip.open(backup_path, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    after = os.path.getsize(backup_path)
    print(f"Backup created: {backup_path}")
    print(f"Size: {before:,} → {after:,} bytes")
    return backup_path


def restore(backup_file=None):
    db_path = os.path.join(DB_DIR, "heat_index.db")
    if backup_file:
        if not os.path.exists(backup_file):
            print(f"ERROR: {backup_file} not found")
            sys.exit(1)
        src = backup_file
    else:
        backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "heat_index_*.db.gz")))
        if not backups:
            print("ERROR: No backups found")
            sys.exit(1)
        src = backups[-1]
        print(f"Using latest backup: {src}")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with gzip.open(src, "rb") as f_in, open(db_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    size = os.path.getsize(db_path)
    print(f"Restored to {db_path} ({size:,} bytes)")
    return db_path


def list_backups():
    if not os.path.exists(BACKUP_DIR):
        print("No backups directory found")
        return
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "heat_index_*.db.gz")))
    if not backups:
        print("No backups found")
        return
    print(f"Found {len(backups)} backup(s):")
    for b in backups:
        size = os.path.getsize(b)
        name = os.path.basename(b)
        if size > 1024 * 1024:
            print(f"  {name}  ({size / (1024**2):.1f} MB)")
        else:
            print(f"  {name}  ({size / 1024:.1f} KB)")


# ── CLI ──────────────────────────────────────────────────────────────────────

USAGE = """Usage: python scripts/db_tools.py <command> [args]

Commands:
  status                  检查数据库状态（表、行数、模式）
  vacuum                  VACUUM 压缩数据库
  archive <year>          归档指定年份之前的数据
  compress                gzip 压缩数据库
  decompress              解压 gzip 数据库
  size                    显示数据库和压缩文件大小
  backup                  创建带日期的 gzip 备份
  restore [backup_file]   从备份恢复（默认最新）
  list                    列出所有备份"""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        check_db_status()
    elif cmd == "vacuum":
        vacuum_db()
    elif cmd == "archive":
        year = int(sys.argv[2]) if len(sys.argv) > 2 else 2020
        archive_before_year(year)
    elif cmd == "compress":
        compress()
    elif cmd == "decompress":
        decompress()
    elif cmd == "size":
        show_size()
    elif cmd == "backup":
        backup()
    elif cmd == "restore":
        restore(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "list":
        list_backups()
    else:
        print(f"Unknown command: {cmd}")
        print(USAGE)
        sys.exit(1)
