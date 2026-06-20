#!/usr/bin/env python3
"""
数据库压缩/解压/备份工具 — 用于 GitHub Actions Cache 和 Release 存储

用法:
  python scripts/db_compress.py compress              # 压缩
  python scripts/db_compress.py decompress            # 解压
  python scripts/db_compress.py size                  # 显示大小
  python scripts/db_compress.py backup                # 带日期的备份
  python scripts/db_compress.py restore <backup>      # 从备份恢复
  python scripts/db_compress.py list                  # 列出所有备份
"""
import sys
import os
import gzip
import shutil
import glob
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.join(DB_DIR, "heat_index.db")
GZ_PATH = DB_PATH + ".gz"
BACKUP_DIR = os.path.join(DB_DIR, "backups")


def compress():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} not found")
        sys.exit(1)
    before = os.path.getsize(DB_PATH)
    with open(DB_PATH, "rb") as f_in, gzip.open(GZ_PATH, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    after = os.path.getsize(GZ_PATH)
    ratio = (1 - after / before) * 100 if before else 0
    print(f"Compressed: {before:,} → {after:,} bytes ({ratio:.1f}% reduction)")
    return GZ_PATH


def decompress():
    if not os.path.exists(GZ_PATH):
        print(f"ERROR: {GZ_PATH} not found")
        sys.exit(1)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with gzip.open(GZ_PATH, "rb") as f_in, open(DB_PATH, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    size = os.path.getsize(DB_PATH)
    print(f"Decompressed to {DB_PATH} ({size:,} bytes)")
    return DB_PATH


def show_size():
    for label, path in [("DB", DB_PATH), ("GZ", GZ_PATH)]:
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


def backup():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} not found")
        sys.exit(1)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"heat_index_{ts}.db.gz")
    before = os.path.getsize(DB_PATH)
    with open(DB_PATH, "rb") as f_in, gzip.open(backup_path, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    after = os.path.getsize(backup_path)
    print(f"Backup created: {backup_path}")
    print(f"Size: {before:,} → {after:,} bytes")
    return backup_path


def restore(backup_file=None):
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
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with gzip.open(src, "rb") as f_in, open(DB_PATH, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    size = os.path.getsize(DB_PATH)
    print(f"Restored to {DB_PATH} ({size:,} bytes)")
    return DB_PATH


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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "size"
    if cmd == "compress":
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
        print("Usage: python scripts/db_compress.py [compress|decompress|size|backup|restore|list]")
        sys.exit(1)
