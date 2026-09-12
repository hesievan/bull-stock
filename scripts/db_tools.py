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
  python scripts/db_tools.py cleanup [--keep N] [--days D] [--bak-keep N] [--dry-run]
                                                       # 清理过期备份（P3-F4）
"""

import sys
import os
import gzip
import shutil
import glob
import sqlite3
import logging
import time
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


ARCHIVE_ALIAS = "arch"
ARCHIVE_TABLES = [
    "stock_daily",
    "index_daily",
    "margin_history",
    "northbound_history",
    "bond_yield",
]


def _archive_table(conn: sqlite3.Connection, tname: str, cutoff: str, alias: str = ARCHIVE_ALIAS) -> int:
    """把主库 ``tname`` 中 ``trade_date < cutoff`` 的行复制到已 ATTACH 的归档库。

    返回本次归档的行数。表结构直接取自**主库** schema，因此不存在旧实现
    "归档库里 `CREATE TABLE ... AS SELECT * FROM tname` 找不到源表 → 抛异常"
    的问题（旧实现每一张表首次归档都失败，随后仍无条件删除，等于删数据不留档）。

    同区间重复归档是幂等的：先清掉归档库中该区间的旧副本再写入。
    """
    cols = [d[1] for d in conn.execute(f"PRAGMA table_info({tname})").fetchall()]
    if not cols:
        raise RuntimeError(f"主库不存在表 {tname}")

    rows = conn.execute(f"SELECT * FROM {tname} WHERE trade_date < ?", (cutoff,)).fetchall()
    if not rows:
        return 0

    # 注意: PRAGMA 不支持 `schema.table` 作为参数, 必须写成 `PRAGMA schema.table_info(table)`
    arch_cols = [d[1] for d in conn.execute(f"PRAGMA {alias}.table_info({tname})").fetchall()]
    if not arch_cols:
        conn.execute(f"CREATE TABLE {alias}.{tname} AS SELECT * FROM {tname} WHERE 1=0")
    elif set(arch_cols) != set(cols):
        raise RuntimeError(
            f"归档表 {tname} 与主库结构不一致（主库 {len(cols)} 列 / 归档 {len(arch_cols)} 列），拒绝写入以免错列"
        )

    conn.execute(f"DELETE FROM {alias}.{tname} WHERE trade_date < ?", (cutoff,))
    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(["?"] * len(cols))
    conn.executemany(f"INSERT INTO {alias}.{tname} ({col_list}) VALUES ({placeholders})", rows)
    logger.info("  Archived %s: %d rows", tname, len(rows))
    return len(rows)


def archive_before_year(year: int, db_path=None) -> bool:
    """归档指定年份之前的数据到独立文件。

    **P0-4**: 归档是删除的前提。任一表归档失败、或归档数与待删数对账不符，
    一律中止删除并返回 False —— 宁可数据留在主库，也不能删了却没留档。
    返回 True 表示归档流程正常结束（含"无数据可归档"）。
    """
    path = db_path or DB_PATH
    cutoff = f"{year}-01-01"
    archive_path = path.replace(".db", f"_archive_{year}.db")

    logger.info("Archiving data before %s to %s", cutoff, archive_path)

    with get_conn(path) as conn:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        tables_to_archive = [t for t in ARCHIVE_TABLES if t in existing]
        missing = [t for t in ARCHIVE_TABLES if t not in existing]
        if missing:
            logger.warning("  库中不存在，跳过: %s", missing)

        total = 0
        for tname in tables_to_archive:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {tname} WHERE trade_date < ?", (cutoff,)).fetchone()[0]
                total += count
            except Exception as e:
                logger.warning("  统计 %s 失败: %s", tname, str(e)[:80])

        if total == 0:
            logger.info("No data to archive")
            return True

        logger.info("Found %d rows to archive", total)

        # ATTACH 归档库到主连接：归档与删除处于同一事务，归档结果可对账
        conn.execute(f"ATTACH DATABASE ? AS {ARCHIVE_ALIAS}", (archive_path,))
        ok = True
        try:
            archived_cnt: dict[str, int] = {}
            failed_tables: list[str] = []
            for tname in tables_to_archive:
                try:
                    archived_cnt[tname] = _archive_table(conn, tname, cutoff)
                except Exception as e:
                    logger.error("  归档 %s 失败: %s", tname, str(e)[:100])
                    failed_tables.append(tname)

            mismatched: list[str] = []
            if not failed_tables:
                for tname in tables_to_archive:
                    expected = conn.execute(f"SELECT COUNT(*) FROM {tname} WHERE trade_date < ?", (cutoff,)).fetchone()[
                        0
                    ]
                    if archived_cnt.get(tname, 0) != expected:
                        mismatched.append(f"{tname}(已归档 {archived_cnt.get(tname, 0)} / 待删 {expected})")

            if failed_tables or mismatched:
                logger.error(
                    "归档未完成，已中止全部删除操作（数据安全优先）: 失败表=%s 对账不符=%s",
                    failed_tables or "无",
                    mismatched or "无",
                )
                ok = False
            else:
                for tname in tables_to_archive:
                    deleted = conn.execute(f"DELETE FROM {tname} WHERE trade_date < ?", (cutoff,)).rowcount
                    if deleted:
                        logger.info("  Deleted from %s: %d rows", tname, deleted)
        finally:
            # 先把归档写入落盘，再解挂；任一失败都不影响主库数据完整性
            try:
                conn.commit()
            except Exception as e:
                logger.warning("归档提交失败: %s", e)
                ok = False
            try:
                conn.execute(f"DETACH DATABASE {ARCHIVE_ALIAS}")
            except Exception as e:
                logger.warning("DETACH %s 失败: %s", ARCHIVE_ALIAS, e)

    if ok:
        logger.info("Archive complete: %s", archive_path)
    return ok


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


# ── 清理过期备份（P3-F4）───────────────────────────────────────────────────────


def cleanup_backups(keep: int = 5, days: int | None = None, dry_run: bool = False, bak_keep: int = 3) -> list:
    """清理过期备份，避免 data/ 无限膨胀。

    - backups/ 下的 gzip 备份：保留最近 `keep` 个（默认 5）；若指定 `days`，
      仅当备份同时位于"超出 keep 的候选"且已超过 `days` 天时才删除（保守语义，
      不会动最近 keep 个以内的备份）。`keep=0` 表示不按个数保留（可全删）。
    - data/ 下的 `.bak_*` 开发期临时备份：保留最近 `bak_keep` 个（默认 3），
      其余删除；`bak_keep=0` 表示全部可删。
    - dry_run=True 只预览不删除。
    返回被删除（或 dry-run 将删除）的文件路径列表。
    """
    removed: list = []
    n_gz_removed = 0

    # 1) 正式备份目录 (backups/heat_index_*.db.gz)
    #    注意: `files[:-keep]` 在 keep=0 时等价 `files[:0]` = []（静默不删），
    #    故 keep<=0 显式取全量作为候选。
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "heat_index_*.db.gz")))
    candidates_gz = backups[:-keep] if keep > 0 else list(backups)
    for b in candidates_gz:
        if days is not None:
            age_days = (time.time() - os.path.getmtime(b)) / 86400
            if age_days < days:
                continue  # 未超期，跳过（即使超出 keep 也保留）
        removed.append(b)
        n_gz_removed += 1

    # 2) 开发期临时备份 (data/*.bak_*)，保留最近 bak_keep 个
    bak_files = sorted(glob.glob(os.path.join(DB_DIR, "*.bak_*")))
    candidates_bak = bak_files[:-bak_keep] if bak_keep > 0 else list(bak_files)
    removed.extend(candidates_bak)

    if dry_run:
        logger.info("cleanup (dry-run): %d file(s) would be removed", len(removed))
    else:
        for b in removed:
            try:
                os.remove(b)
            except OSError as e:
                logger.warning("failed to remove %s: %s", b, e)
        logger.info(
            "cleanup: removed %d file(s) (backups=%d, temp_bak=%d), kept %d backup(s) / %d temp_bak(s)",
            len(removed),
            n_gz_removed,
            len(removed) - n_gz_removed,
            len(backups) - n_gz_removed,
            len(bak_files) - len(candidates_bak),
        )

    for b in removed:
        logger.info("  %s %s", "WOULD REMOVE" if dry_run else "removed", os.path.basename(b))
    return removed


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
  list                    列出所有备份
  cleanup [--keep N] [--days D] [--bak-keep N] [--dry-run]   清理过期备份
    (backups/ 保留最近 N 个, 指定 --days 时超期才删; data/*.bak_* 保留最近 N 个)"""

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
        if not archive_before_year(year):
            sys.exit(1)  # 归档未完成 → 非零退出，避免 CI/脚本误以为成功
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
    elif cmd == "cleanup":
        keep = 5
        days = None
        bak_keep = 3
        dry_run = False
        rest = sys.argv[2:]
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--keep" and i + 1 < len(rest):
                keep = int(rest[i + 1])
                i += 2
            elif a == "--days" and i + 1 < len(rest):
                days = int(rest[i + 1])
                i += 2
            elif a == "--bak-keep" and i + 1 < len(rest):
                bak_keep = int(rest[i + 1])
                i += 2
            elif a == "--dry-run":
                dry_run = True
                i += 1
            else:
                i += 1
        cleanup_backups(keep=keep, days=days, dry_run=dry_run, bak_keep=bak_keep)
    else:
        print(f"Unknown command: {cmd}")
        print(USAGE)
        sys.exit(1)
