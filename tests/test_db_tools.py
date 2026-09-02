"""Tests for scripts/db_tools.py — 备份清理 cleanup_backups (P3-F4 #13)。

覆盖: dry-run 零删除 / 按个数保留 / keep=0 边界(修复 `files[:-0]`=[] 静默失效) /
days 保护未超期候选 / bak_keep 参数化。全部用 tmp 目录 + monkeypatch DB_DIR/BACKUP_DIR,
不触碰真实 data/。
"""

import gzip
import os
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
