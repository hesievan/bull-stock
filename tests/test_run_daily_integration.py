"""集成测试：run_daily 端到端冒烟（无网络）。

通过把 fetcher 的网络抓取函数与指标计算函数全部替换为 no-op / 合成值，
在临时数据库上跑一遍 run_daily 主流程，验证编排（_run_step）、JSON 落盘
（index.json / run_status.json）与异常容错都不崩溃。

真实 web/data 在测试前备份、结束后还原，避免污染仓库产物。
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest


WEB_DATA = Path(__file__).parent.parent / "web" / "data"

FAKE_RESULT = {
    "trade_date": "2026-08-11",
    "composite_score": 60.0,
    "dimensions": {
        "valuation": {"score": 70, "label": "估值"},
        "fund": {"score": 60, "label": "资金"},
        "sentiment": {"score": 65, "label": "情绪"},
        "structure": {"score": 50, "label": "结构"},
    },
    "indicators_v2": {"pe": 80.0, "buffett": 70.0},
    "indicators": {"pe": 80.0, "buffett": 70.0, "qvix": 20.0, "qvix_components": {}},
    "indicator_raw": {"pe": 15.0, "buffett": 0.8},
}


@pytest.fixture
def _web_data_backup():
    tmp = Path(tempfile.mkdtemp(prefix="webdata_bak_"))
    if WEB_DATA.exists():
        shutil.copytree(WEB_DATA, tmp / "data")
    yield
    # 还原被测试覆盖的文件
    src = tmp / "data"
    if src.exists():
        for p in src.iterdir():
            if p.is_file():
                shutil.copy2(p, WEB_DATA / p.name)
    shutil.rmtree(tmp, ignore_errors=True)


def _mock_external(monkeypatch):
    # 1) fetcher 的所有网络抓取 / 落库函数 → no-op
    import src.data.fetcher as fetcher

    for name in dir(fetcher):
        if name.startswith("fetch_") or name in ("_save",):
            attr = getattr(fetcher, name)
            if callable(attr):
                monkeypatch.setattr(fetcher, name, lambda *a, **k: None, raising=False)

    # 2) 指标计算 → 合成结果（保证 S5/S55/S7/S75 不依赖真实数据）
    import src.indicators.heat_index_v2 as hv2

    monkeypatch.setattr(hv2, "compute_index_v2", lambda *a, **k: dict(FAKE_RESULT), raising=False)

    import src.indicators.index_heat as ih

    monkeypatch.setattr(ih, "compute_index_heat", lambda *a, **k: [], raising=False)

    import src.indicators.sector_calculator as sc

    monkeypatch.setattr(sc, "calculate_sector_heat", lambda *a, **k: [], raising=False)

    import src.indicators.focus_industries as fi

    monkeypatch.setattr(
        fi, "compute_focus_industries", lambda *a, **k: {"trade_date": "2026-08-11", "industries": []}, raising=False
    )


def test_run_daily_e2e_smoke(monkeypatch, _web_data_backup):
    import src.data.database as database

    # 临时数据库（patch 在 run_daily 内 import 时读取的模块属性）
    tmp_db = Path(tempfile.mktemp(suffix=".db"))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_db))

    _mock_external(monkeypatch)

    from scripts.run_daily import run_daily

    # 不应抛异常
    run_daily(trade_date="2026-08-11")

    # run_status.json 必须产出且结构正确
    status_path = WEB_DATA / "run_status.json"
    assert status_path.exists(), "run_status.json 未生成"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert "steps" in status
    assert "schema_version" in status
    assert "python_version" in status
    assert status["trade_date"] == "2026-08-11"

    # index.json 必须产出（含综合分）
    index_path = WEB_DATA / "index.json"
    assert index_path.exists(), "index.json 未生成"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["composite_score"] == 60.0
    assert index["level"] == "orange"

    # 临时 DB 清理
    if tmp_db.exists():
        tmp_db.unlink()
