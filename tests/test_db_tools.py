"""Tests for scripts/db_tools.py — 备份清理 cleanup_backups (P3-F4 #13)。

覆盖: dry-run 零删除 / 按个数保留 / keep=0 边界(修复 `files[:-0]`=[] 静默失效) /
days 保护未超期候选 / bak_keep 参数化。全部用 tmp 目录 + monkeypatch DB_DIR/BACKUP_DIR,
不触碰真实 data/。
"""

import gzip
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import scripts.db_tools as db_tools  # noqa: E402


def _mk_gz_backup(dirpath, name, age_days):
    """在 dirpath 下创建一个 gzip 备份文件，可指定“多少天前”的 mtime。"""
    p = Path(dirpath) / name
    with gzip.open(p, "wb") as f:
        f.write(b"x")
    ts = time.time() - age_days * 86400
    os.utime(p, (ts, ts))
    return p


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    """把 DB_DIR/BACKUP_DIR 指向临时目录，返回 (data_dir, backups_dir)。"""
    data_dir = tmp_path / "data"
    backups_dir = data_dir / "backups"
    backups_dir.mkdir(parents=True)
    monkeypatch.setattr(db_tools, "DB_DIR", str(data_dir))
    monkeypatch.setattr(db_tools, "BACKUP_DIR", str(backups_dir))
    return data_dir, backups_dir


def _surviving_gz(backups_dir):
    return sorted(p.name for p in Path(backups_dir).glob("heat_index_*.db.gz"))


def _surviving_bak(data_dir):
    return sorted(p.name for p in Path(data_dir).glob("*.bak_*"))


class TestCleanupBackups:
    def test_dry_run_removes_nothing(self, backup_env):
        data_dir, backups_dir = backup_env
        gz_names = [f"heat_index_2026010{i}.db.gz" for i in range(1, 9)]  # 8 个
        bak_names = [f"heat_index.db.bak_tmp{i}" for i in range(1, 7)]  # 6 个
        for n in gz_names:
            _mk_gz_backup(backups_dir, n, age_days=1)
        for n in bak_names:
            _mk_gz_backup(data_dir, n, age_days=1)

        removed = db_tools.cleanup_backups(dry_run=True)

        # 默认 keep=5 / bak_keep=3 → 预览 3 + 3 = 6 个
        assert len(removed) == 6
        # 预览不删除任何文件
        assert len(_surviving_gz(backups_dir)) == 8
        assert len(_surviving_bak(data_dir)) == 6

    def test_keeps_newest_by_count(self, backup_env):
        data_dir, backups_dir = backup_env
        for i in range(1, 9):  # 8 gz + 6 bak
            _mk_gz_backup(backups_dir, f"heat_index_2026010{i}.db.gz", age_days=1)
        for i in range(1, 7):
            _mk_gz_backup(data_dir, f"heat_index.db.bak_tmp{i}", age_days=1)

        db_tools.cleanup_backups()

        # gz 保留最近 5 个 (20260104..08); .bak_* 保留最近 3 个 (tmp4..6)
        assert _surviving_gz(backups_dir) == [f"heat_index_2026010{i}.db.gz" for i in range(4, 9)]
        assert _surviving_bak(data_dir) == [f"heat_index.db.bak_tmp{i}" for i in range(4, 7)]

    def test_keep_zero_removes_all(self, backup_env):
        """keep=0 / bak_keep=0 → 全部可删 (回归: `files[:-0]`=[] 曾静默不删)。"""
        data_dir, backups_dir = backup_env
        for i in range(1, 9):
            _mk_gz_backup(backups_dir, f"heat_index_2026010{i}.db.gz", age_days=1)
        for i in range(1, 7):
            _mk_gz_backup(data_dir, f"heat_index.db.bak_tmp{i}", age_days=1)

        removed = db_tools.cleanup_backups(keep=0, bak_keep=0)

        assert len(removed) == 14
        assert _surviving_gz(backups_dir) == []
        assert _surviving_bak(data_dir) == []

    def test_days_skips_candidate_not_yet_expired(self, backup_env):
        """超出 keep 但未超过 days 的备份应保留 (保守语义)。"""
        _, backups_dir = backup_env
        _mk_gz_backup(backups_dir, "heat_index_20260101.db.gz", age_days=40)  # 超期 → 删
        _mk_gz_backup(backups_dir, "heat_index_20260102.db.gz", age_days=5)  # 超 keep 但未超期 → 留
        _mk_gz_backup(backups_dir, "heat_index_20260103.db.gz", age_days=0)  # 最新 → 留

        removed = db_tools.cleanup_backups(keep=1, days=10)

        assert [Path(b).name for b in removed] == ["heat_index_20260101.db.gz"]
        assert _surviving_gz(backups_dir) == [
            "heat_index_20260102.db.gz",
            "heat_index_20260103.db.gz",
        ]

    def test_bak_keep_param(self, backup_env):
        """--bak-keep 参数化: 只留最近 N 个 .bak_*。"""
        data_dir, backups_dir = backup_env
        for i in range(1, 6):  # 仅 .bak_* (无 gz 备份)
            _mk_gz_backup(data_dir, f"heat_index.db.bak_tmp{i}", age_days=1)

        removed = db_tools.cleanup_backups(keep=0, bak_keep=2)

        assert len(removed) == 3
        assert _surviving_bak(data_dir) == ["heat_index.db.bak_tmp4", "heat_index.db.bak_tmp5"]


# ── P0-4: 归档失败必须中止删除 ───────────────────────────────────────────────


def _mk_archive_db(path, rows: int = 3, tables=None) -> None:
    """造一个含归档目标表的小库，日期统一落在 2018-01-xx（早于 2020 截止线）。"""
    tables = tables or db_tools.ARCHIVE_TABLES
    conn = sqlite3.connect(path)
    for t in tables:
        conn.execute(f"CREATE TABLE {t} (trade_date TEXT, value REAL)")
        for i in range(rows):
            conn.execute(f"INSERT INTO {t} VALUES (?, ?)", (f"2018-01-{i + 1:02d}", float(i)))
    conn.commit()
    conn.close()


def _count(db_path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


class TestArchiveBeforeYear:
    def test_success_moves_rows(self, tmp_path):
        """正常路径: 行被复制到归档库并从主库删除。

        回归点: 旧实现用 `CREATE TABLE IF NOT EXISTS t AS SELECT * FROM t` 在**归档连接**
        上建表，源表在归档库里并不存在 → 每张表首次归档都抛异常，却仍无条件执行删除。
        """
        db = tmp_path / "heat_index.db"
        _mk_archive_db(db, rows=3)

        assert db_tools.archive_before_year(2020, db_path=str(db)) is True

        for t in db_tools.ARCHIVE_TABLES:
            assert _count(db, t) == 0, f"{t} 未删除"
        archive = tmp_path / "heat_index_archive_2020.db"
        assert archive.exists()
        for t in db_tools.ARCHIVE_TABLES:
            assert _count(archive, t) == 3, f"{t} 未归档"

    def test_archive_failure_aborts_delete(self, tmp_path, monkeypatch):
        """任一表归档失败 → 所有表都不得删除（旧实现会照删不误）。"""
        db = tmp_path / "heat_index.db"
        _mk_archive_db(db, rows=3)

        real_archive = db_tools._archive_table

        def flaky(conn, tname, cutoff, alias=db_tools.ARCHIVE_ALIAS):
            if tname == "bond_yield":
                raise RuntimeError("simulated archive failure")
            return real_archive(conn, tname, cutoff, alias)

        monkeypatch.setattr(db_tools, "_archive_table", flaky)

        assert db_tools.archive_before_year(2020, db_path=str(db)) is False

        for t in db_tools.ARCHIVE_TABLES:
            assert _count(db, t) == 3, f"{t} 在归档失败的情况下仍被删除"

    def test_reconciliation_mismatch_aborts_delete(self, tmp_path, monkeypatch):
        """归档数 ≠ 待删数（如归档写了一半）→ 中止删除。"""
        db = tmp_path / "heat_index.db"
        _mk_archive_db(db, rows=5)

        real_archive = db_tools._archive_table

        def underreport(conn, tname, cutoff, alias=db_tools.ARCHIVE_ALIAS):
            real_archive(conn, tname, cutoff, alias)
            return 0  # 谎报归档 0 行 → 对账不符

        monkeypatch.setattr(db_tools, "_archive_table", underreport)

        assert db_tools.archive_before_year(2020, db_path=str(db)) is False
        assert _count(db, "stock_daily") == 5

    def test_no_data_to_archive(self, tmp_path):
        """截止线之前无数据 → 直接返回 True，不产生归档文件。"""
        db = tmp_path / "heat_index.db"
        _mk_archive_db(db, rows=3)

        assert db_tools.archive_before_year(2015, db_path=str(db)) is True
        assert _count(db, "stock_daily") == 3
        assert not (tmp_path / "heat_index_archive_2015.db").exists()

    def test_missing_tables_are_skipped(self, tmp_path):
        """只存在部分表时，缺失表跳过而非整单中止。"""
        db = tmp_path / "heat_index.db"
        _mk_archive_db(db, rows=2, tables=["stock_daily"])

        assert db_tools.archive_before_year(2020, db_path=str(db)) is True
        assert _count(db, "stock_daily") == 0
        assert _count(tmp_path / "heat_index_archive_2020.db", "stock_daily") == 2

    def test_idempotent_rerun(self, tmp_path):
        """重复归档同一区间不产生重复副本（归档库里先清区间再写）。"""
        db = tmp_path / "heat_index.db"
        _mk_archive_db(db, rows=4)

        assert db_tools.archive_before_year(2020, db_path=str(db)) is True
        # 再灌一批同区间数据，模拟重复执行
        conn = sqlite3.connect(db)
        for i in range(2):
            conn.execute("INSERT INTO stock_daily VALUES (?, ?)", (f"2019-06-0{i + 1}", 1.0))
        conn.commit()
        conn.close()

        assert db_tools.archive_before_year(2020, db_path=str(db)) is True
        assert _count(tmp_path / "heat_index_archive_2020.db", "stock_daily") == 2
