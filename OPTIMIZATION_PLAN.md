# 优化建议 — P0~P3 分级清单

> 基于 2026-06-14 项目审查，按紧急度和影响排序。
> 每条建议标注: **文件** → **改动** → **理由**

---

## P0 — 立即修复 (本周内)

阻塞性问题，不修复会影响 CI 运行或引入静默错误。

### 1. 修复 GitHub Actions 依赖缺失

**文件**: `.github/workflows/daily.yml:28`

**当前**:
```yaml
- name: Install dependencies
  run: |
    pip install akshare pandas numpy
```

**问题**: 缺少 `tushare` 和 `pyyaml`，CI 运行时必然报 `ModuleNotFoundError`。

**改为**:
```yaml
- name: Install dependencies
  run: |
    pip install -r requirements.txt
```

同时确保 `requirements.txt` 包含所有依赖:
```
tushare>=1.4.0
akshare>=1.15.0
pandas>=2.0.0
numpy>=1.24.0
pyyaml>=6.0
```

**理由**: CI 是自动化的核心，依赖缺失意味着每日计算完全失效。

---

### 2. 统一数据库连接 — 消除绕过 get_conn() 的裸连接

**涉及文件**:
- `src/data/fetcher.py:325` — `fetch_daily_basic_to_stock_daily` 用 `sqlite3.connect()`
- `src/indicators/calculator.py:98` — `_conn()` 方法用 `sqlite3.connect()`
- `src/indicators/calculator.py:1262` — `calculate_sector_heat` 用 `sqlite3.connect()`

**当前问题**: `database.py` 提供了 `get_conn()` 上下文管理器(带 commit/rollback/WAL)，但这三处绕过了它，导致:
- 无事务保护(写入失败不回滚)
- 无 WAL 模式设置
- 连接可能未关闭(异常路径)

**改动方案**:

(a) `fetcher.py:314-408` — 将整个函数改为使用 `get_conn()`:
```python
def fetch_daily_basic_to_stock_daily(trade_date: str, db_path: str = None) -> int:
    from src.data.database import get_conn, DB_PATH as _DB
    _db = db_path or _DB
    with get_conn(_db) as conn:
        # ... 原有逻辑，用 conn 代替 self._db_conn
```

(b) `calculator.py:94-99` — 删除 `_conn()` 方法，改为惰性加载复用连接:
```python
def _get_conn(self):
    if not hasattr(self, "_db_conn") or self._db_conn is None:
        self._db_conn = sqlite3.connect(self.db_path)
        self._db_conn.execute("PRAGMA journal_mode=WAL")
    return self._db_conn
```
并在 `__del__` 或提供 `close()` 方法关闭。

(c) `calculator.py:1262` — `calculate_sector_heat` 改为:
```python
with get_conn(db_path) as conn:
    # 原有逻辑
```

**理由**: 裸连接是数据损坏的潜在根源，尤其在异常中断时。

---

### 3. 添加 pyproject.toml — 标准化包管理

**新建文件**: `pyproject.toml`

```toml
[project]
name = "bull-market-heat-index"
version = "3.4.0"
description = "A股牛市热度指数"
requires-python = ">=3.10"
dependencies = [
    "tushare>=1.4.0",
    "akshare>=1.15.0",
    "pandas>=2.0.0",
    "numpy>=1.24.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "ruff>=0.1.0",
    "mypy>=1.0",
]

[tool.ruff]
line-length = 120
select = ["E", "F", "W"]

[tool.mypy]
python_version = "3.10"
ignore_missing_imports = true
```

**理由**: 当前无标准包定义，无法 `pip install -e .`，也无法被其他项目引用。

---

### 4. 补充核心指标单元测试

**新建文件**: `tests/test_calculator.py`

至少覆盖以下场景:

```python
import pytest
from src.indicators.calculator import HeatIndexCalculator, _pct_rank, _pct_rank_inv

class TestPctRank:
    def test_basic(self):
        import pandas as pd
        s = pd.Series([1, 2, 3, 4, 5])
        assert _pct_rank(s, 3) == 0.4  # 2/5 < 3
        assert _pct_rank(s, 5) == 0.8  # 4/5 < 5

    def test_empty_series(self):
        import pandas as pd
        assert _pct_rank(pd.Series([]), 1) is not None  # 返回 nan

    def test_nan_value(self):
        import pandas as pd
        import numpy as np
        assert np.isnan(_pct_rank(pd.Series([1,2,3]), float('nan')))

class TestHeatIndexCalculator:
    def test_init_default_date(self):
        calc = HeatIndexCalculator()
        assert calc.trade_date is not None

    def test_combine_dimension_all_none(self):
        calc = HeatIndexCalculator()
        assert calc._combine_dimension([None, None], "test") is None

    def test_combine_dimension_valid(self):
        calc = HeatIndexCalculator()
        result = calc._combine_dimension([60.0, 80.0], "test")
        assert result == 70.0

    def test_combine_dimension_with_nan(self):
        calc = HeatIndexCalculator()
        import numpy as np
        result = calc._combine_dimension([60.0, np.nan, 80.0], "test")
        assert result == 70.0
```

**理由**: 无测试 = 任何改动都是盲改。核心计算逻辑必须有回归保障。

---

## P1 — 短期优化 (1-2周)

提升代码质量和可维护性。

### 5. 拆分 calculator.py — 主指数与板块热度分离

**当前**: `calculator.py` 1360行，包含两个独立引擎:
- `HeatIndexCalculator` (行55-1126) — 市场级热度
- `calculate_sector_heat` (行1128-1360) — 板块级热度

**改动**:
- 新建 `src/indicators/sector_calculator.py`，移入板块相关代码
- `calculator.py` 保留主指数引擎，行数降至 ~1100行
- 更新 `scripts/run_daily.py:185` 的 import

**理由**: 单文件1360行难以导航和维护，两个引擎职责完全不同。

---

### 6. 清理 scripts/ 目录 — 合并重复脚本

**当前 scripts/ 有 30+ 文件**，功能重叠:

| 重复组 | 文件 | 建议 |
|--------|------|------|
| 回填脚本 | `backfill_history.py`, `backfill_tushare.py`, `backfill_weekly.py`, `backfill_margin_2015.py`, `backfill_full_market_pe.py` | 合并为 `scripts/backfill.py`，用子命令区分 |
| 数据获取 | `fetch_daily_basic.py`, `fetch_tushare_history.py`, `fetch_ah_premium.py`, `fetch_hist_constituents.py`, `fetch_index_constituents.py` | 合并为 `scripts/fetch.py` |
| 分步脚本 | `step1_bond_yield.py` ~ `step5b_circ_mv_by_stock.py` | 已被 `run_daily.py` 取代，可删除 |
| 验证脚本 | `verify_all_fixes.py`, `verify_p0_fix.py` | 保留，但统一为 `scripts/verify.py` |
| 分析脚本 | `analyze_peaks.py`, `analyze_peaks2.py` | 合并为 `scripts/analyze.py` |

**改动**: 创建 `scripts/backfill.py`:
```python
"""统一回填入口"""
# python scripts/backfill.py margin --start 2015-01-01
# python scripts/backfill.py full --start 2015-01-01
# python scripts/backfill.py weekly
```

**理由**: 30+脚本目录混乱，新开发者无法判断哪个是入口。

---

### 7. 配置 TUSHARE_TOKEN 加载路径

**文件**: `scripts/run_daily.py:19-24`

**当前**: 硬编码从 `~/daily_stock_analysis/.env` 读取，路径不通用。

**改为**:
```python
def _load_env():
    """按优先级加载 .env: 环境变量 > 项目根目录 .env > ~/daily_stock_analysis/.env"""
    if os.environ.get("TUSHARE_TOKEN"):
        return
    candidates = [
        Path(__file__).parent.parent / ".env",
        Path.home() / "daily_stock_analysis" / ".env",
    ]
    for p in candidates:
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("TUSHARE_TOKEN="):
                    os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1]
                    return
```

**理由**: 当前路径假设用户目录结构，换机器/换用户会失败。

---

### 8. 修复 fetcher.py 中 fetch_m2_history 忽略参数

**文件**: `src/data/fetcher.py:413-423`

**当前**:
```python
def fetch_m2_history(start: str = "2008-01-01", end: str = None):
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()  # 忽略了 start/end
```

**改为**: 调用后按 start/end 过滤:
```python
def fetch_m2_history(start: str = "2008-01-01", end: str = None):
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()
        if df is None or df.empty:
            return
        df.columns = ["month", "m2_billion", "m2_yoy", "m1_billion", "m1_yoy", "m0_billion", "m0_yoy"]
        df["month"] = pd.to_datetime(df["month"]).dt.strftime("%Y-%m")
        if start:
            df = df[df["month"] >= start[:7]]
        if end:
            df = df[df["month"] <= end[:7]]
        _save(df, "m2_monthly")
    except Exception as e:
        logger.error("fetch_m2_history failed: %s", str(e)[:80])
```

**理由**: 参数声明了但不用，调用方误以为支持时间范围过滤。

---

### 9. 添加数据库版本迁移机制

**文件**: `src/data/database.py`

**当前**: `SCHEMA` 建表用 `CREATE TABLE IF NOT EXISTS`，无法处理字段变更。

**改动**: 在 `metadata` 表中增加版本号:
```python
SCHEMA_VERSION = 2

def init_database(db_path=None):
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        # 版本检查
        try:
            ver = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            current_ver = int(ver[0]) if ver else 1
        except:
            current_ver = 1
        
        if current_ver < SCHEMA_VERSION:
            _migrate(conn, current_ver)
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value,updated_at) VALUES('schema_version',?,datetime('now'))",
                (str(SCHEMA_VERSION),)
            )

def _migrate(conn, from_ver):
    if from_ver < 2:
        # 示例: 添加新列
        # conn.execute("ALTER TABLE heat_index ADD COLUMN dim_macro REAL")
        pass
```

**理由**: 未来改表结构时，无迁移机制会导致数据丢失或运行错误。

---

### 10. 添加 ruff lint 到 CI

**文件**: `.github/workflows/daily.yml`

在 install 步骤后添加:
```yaml
- name: Lint
  run: |
    pip install ruff
    ruff check src/ scripts/
    ruff format --check src/ scripts/
```

**理由**: 当前无任何代码风格检查，不一致的代码逐渐累积。

---

## P2 — 中期改进 (1个月)

提升指标质量和用户体验。

### 11. 增加动量指标 — 60日涨幅分位

**文件**: `src/indicators/calculator.py`

**新增方法**: `_calc_momentum_60d()`

```python
def _calc_momentum_60d(self) -> Optional[float]:
    """60日涨幅历史分位 — 捕捉趋势强度"""
    try:
        idx = self._get_index_daily()
        sh = idx[idx["index_code"] == "sh000001"].sort_values("trade_date")
        if len(sh) < 120:
            return None
        sh["close"] = pd.to_numeric(sh["close"], errors="coerce")
        # 60日涨幅
        pct_60d = sh["close"].pct_change(60).dropna() * 100
        if len(pct_60d) < 60:
            return None
        cur = pct_60d.iloc[-1]
        score = _pct_rank(pct_60d, cur) * 100
        logger.info("Momentum 60d: %.2f%%, score=%.1f", cur, score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.error("Momentum 60d calc failed: %s", e)
        return None
```

**改动**: 在 `calculate()` 方法中加入技术维度:
```python
# 技术 (3项, 权重10%)
t1 = self._calc_ma_alignment()
t3 = self._calc_deviation_ma250()
t4 = self._calc_momentum_60d()  # 新增
dim_tech = self._combine_dimension([t1, t3, t4], "Technical")
```

**理由**: 当前技术维度仅2项(MA排列+偏离度)，区分度最差(27.7)，动量是经典的趋势指标。

---

### 12. 数据库压缩 — 解决 1.9GB 单文件

**当前**: `heat_index.db` 1.9GB，主要是 `stock_daily` 表(5500只×2772天≈1500万行)。

**方案**:

(a) **WAL checkpoint** — 立即可做:
```python
# 在 init_database() 末尾添加
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
```

(b) **归档旧数据** — 将2020年以前的数据移到 archive:
```python
# 新建 scripts/archive_old_data.py
def archive_before(year=2020):
    with get_conn() as conn:
        cutoff = f"{year}-01-01"
        # 导出到 archive_stock_daily.db
        # 删除原表中 cutoff 之前的数据
        conn.execute("DELETE FROM stock_daily WHERE trade_date < ?", (cutoff,))
        conn.execute("VACUUM")
```

(c) **改用 Parquet 存储历史数据** — 长期方案:
- `stock_daily` 按年分片存储为 Parquet
- SQLite 只保留最近2年热数据

**理由**: 1.9GB 备份/迁移困难，GitHub repo 不应包含大文件。

---

### 13. 修复 stock_industry 空值 — 321只股票

**文件**: `src/data/fetcher.py` 或新建 `scripts/fix_industry.py`

**方案**: 从 tushare `stock_basic` 重新拉取:
```python
def fix_industry_nulls():
    """修复 stock_industry 中 industry 为空的记录"""
    from src.data.database import get_conn, save_dataframe
    
    with get_conn() as conn:
        null_stocks = pd.read_sql(
            "SELECT code FROM stock_industry WHERE industry IS NULL OR industry = ''",
            conn
        )
    
    if null_stocks.empty:
        return
    
    pro = _get_pro()
    # 用 ts_code 格式查询
    ts_codes = [ak_to_ts(c) for c in null_stocks["code"].tolist()]
    
    for ts_code in ts_codes:
        try:
            df = pro.stock_basic(ts_code=ts_code, fields="ts_code,industry")
            if df is not None and not df.empty:
                industry = df.iloc[0]["industry"]
                ak_code = ts_to_ak(ts_code)
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE stock_industry SET industry=? WHERE code=?",
                        (industry, ak_code)
                    )
        except:
            pass
```

**理由**: 行业热度计算时丢弃321只股票，影响板块评分准确性。

---

### 14. 飞书 Webhook URL 从代码中移除

**文件**: `src/output/json_writer.py:19-21`

**当前**:
```python
FEISHU_WEBHOOK = os.environ.get(
    "FEISHU_WEBHOOK",
    "https://www.feishu.cn/flow/api/trigger-webhook/18d944beda7772e52c8e326e34b40da0"
)
```

**改为**:
```python
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
```

同时在 `config/default.yaml` 中添加:
```yaml
notification:
  feishu_webhook: ""  # 通过环境变量 FEISHU_WEBHOOK 设置
```

**理由**: 硬编码的 Webhook URL 是安全风险，且换 URL 需改代码。

---

### 15. 添加多环境配置

**新建文件**: `config/dev.yaml`, `config/prod.yaml`

```yaml
# config/dev.yaml
data:
  db_path: "data/heat_index_dev.db"
notification:
  feishu_webhook: ""  # 开发环境不推送

# config/prod.yaml
data:
  db_path: "data/heat_index.db"
```

**改动**: `src/config.py` 支持环境选择:
```python
ENV = os.environ.get("HEAT_INDEX_ENV", "prod")
CONFIG_PATH = os.environ.get(
    "HEAT_INDEX_CONFIG",
    BASE_DIR / "config" / f"{ENV}.yaml"
)
```

**理由**: 当前只有一套配置，开发/生产无法隔离。

---

## P3 — 长期规划 (3个月+)

架构升级和功能扩展。

### 16. VIX 替代 — 50ETF 期权隐含波动率

**当前**: 用上证综指20日收益率标准差替代 VIX，不够灵敏。

**方案**: 从 akshare 获取 50ETF 期权数据:
```python
import akshare as ak
# 50ETF期权隐含波动率
df = ak.option_50etf_qvix()  # 或类似接口
```

**影响**: 情绪维度新增1个高价值指标，恐慌度量更准确。

---

### 17. API 服务化 — 提供 REST 接口

**新建**: `src/api/` 模块

```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/api/heat")
def get_heat_index():
    """返回最新热度指数"""
    ...

@app.get("/api/history")
def get_history(days=30):
    """返回历史数据"""
    ...

@app.get("/api/sectors")
def get_sectors():
    """返回板块热度"""
    ...
```

**影响**: 支持外部系统(如量化交易系统)调用。

---

### 18. 回测自动化 — CI 中添加验证

**文件**: `.github/workflows/daily.yml`

添加步骤:
```yaml
- name: Backtest validation
  run: |
    python scripts/backtest.py --quick  # 快速回测最近30天
```

`scripts/backtest.py` 改为:
```python
def quick_validate(days=30):
    """快速验证: 对比最近N天的计算结果是否一致"""
    # 1. 取 history.json 中最近N天
    # 2. 对每天重新计算
    # 3. 对比分数差异 < 0.5 则通过
```

**意义**: 每次部署前自动验证指标计算正确性。

---

### 19. Web 前端响应式优化

**文件**: `web/app.html`

当前前端仅桌面端适配，需要:
- 移动端断点 (`@media (max-width: 768px)`)
- 维度卡片改为2列
- 仪表盘缩小
- 板块列表改为单列

**影响**: 支持手机查看，提升日常使用体验。

---

### 20. 敏感信息管理 — 迁移到 Secret Manager

**当前**: TUSHARE_TOKEN 通过环境变量/.env 文件传递。

**方案**: 如果部署在云上，迁移到:
- AWS: Secrets Manager
- 阿里云: KMS
- 本地: `keyring` 库

```python
import keyring
TUSHARE_TOKEN = keyring.get_password("bull-market-heat-index", "tushare_token")
```

**影响**: 防止 token 泄露到日志/代码中。

---

## 执行顺序建议

```
Week 1:  P0-1(CI修复) + P0-2(连接统一) + P0-3(pyproject.toml)
Week 2:  P0-4(单元测试) + P1-5(拆分calculator) + P1-7(配置加载)
Week 3:  P1-6(清理scripts) + P1-8(修复m2) + P1-9(数据库迁移)
Week 4:  P1-10(lint CI) + P2-11(动量指标) + P2-13(修复行业空值)
Month 2: P2-12(数据库压缩) + P2-14(Webhook清理) + P2-15(多环境)
Month 3: P3-16(VIX) + P3-18(回测自动化)
```

---

*优化建议 v1.0 · 2026-06-14*
