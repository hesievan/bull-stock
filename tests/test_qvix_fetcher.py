"""Tests for src/data/qvix_fetcher.py — QVIX 恐慌指数抓取

网络相关 (_download_csv) 用 monkeypatch mock, 验证:
  - fetch_qvix_data 在 mock 下返回含 50index/300index/1000index 列的 DataFrame
  - compute_panic_index 加权合成正确 (panic_index / concentration)
"""

import pandas as pd
import pytest

from src.data import qvix_fetcher as qf

IDX = pd.to_datetime(["2026-08-10", "2026-08-11"])


@pytest.fixture
def fake_raw():
    # 构造与真实 optbbs CSV 列数一致的 raw (QVIX_COLUMNS 最大列索引=82 → 需 ≥83 列)
    cols = [f"c{i}" for i in range(83)]
    data = {c: [0.0] * 2 for c in cols}
    # 注入三个品种需要的 close 列 (按 QVIX_COLUMNS 列索引)
    data["c0"] = ["2026-08-10", "2026-08-11"]  # date 列
    data["c82"] = [20.0, 21.0]  # 50index close
    data["c20"] = [19.0, 22.0]  # 300index close
    data["c28"] = [21.0, 26.0]  # 1000index close
    return pd.DataFrame(data)


class TestQvixFetch:
    def test_fetch_qvix_data_mocked(self, fake_raw, monkeypatch):
        monkeypatch.setattr(qf, "_download_csv", lambda timeout=60: fake_raw)
        out = qf.fetch_qvix_data()
        assert isinstance(out, pd.DataFrame)
        assert list(out.columns) == ["50index", "300index", "1000index"]
        assert len(out) == 2
        # close 值正确提取
        assert out["50index"].tolist() == [20.0, 21.0]
        assert out["300index"].tolist() == [19.0, 22.0]
        assert out["1000index"].tolist() == [21.0, 26.0]

    def test_compute_panic_index_weights(self):
        qvix_df = pd.DataFrame(
            {"50index": [18.0, 22.0], "300index": [19.0, 23.0], "1000index": [21.0, 26.0]},
            index=IDX,
        )
        out = qf.compute_panic_index(qvix_df)
        assert "panic_index" in out.columns
        assert "concentration" in out.columns
        # panic_index = 0.3*x50 + 0.4*x300 + 0.3*x1000
        assert out["panic_index"].iloc[0] == pytest.approx(0.3 * 18 + 0.4 * 19 + 0.3 * 21)
        # concentration = 1000 - 50
        assert out["concentration"].iloc[0] == pytest.approx(21.0 - 18.0)
        # 列已重命名为 qvix_*
        assert "qvix_50" in out.columns and "qvix_1000" in out.columns
