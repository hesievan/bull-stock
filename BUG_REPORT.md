# Bug 评估报告

> 审查时间: 2026-06-14 | 项目版本: v3.5
> 审查范围: calculator.py, database.py, fetcher.py, json_writer.py, run_daily.py, sector_calculator.py, config.py

---

## P0 — 立即修复 (影响计算正确性)

### ~~BUG-1: pct_change() 产生 inf 值未过滤~~ ✅ 已修复

**文件**: `src/indicators/calculator.py:360, 433`
**影响**: `_calc_margin_ratio` 和 `_calc_northbound_cumflow` 中

```python
merged["change_rate"] = merged["ratio"].pct_change() * 100
# 当前一行的 ratio=0 时，pct_change() 产生 inf
# 后续 dropna() 只过滤 NaN，不过滤 inf
# 结果: cur_cr=inf 时，分位永远=1.0，得分永远=100
```

**修复**:
```python
merged["change_rate"] = merged["ratio"].pct_change() * 100
merged["change_rate"] = merged["change_rate"].replace([np.inf, -np.inf], np.nan)
```

---

### ~~BUG-2: _pct_rank 对全 NaN 序列返回 0.0 而非 NaN~~ ✅ 已修复

**文件**: `src/indicators/calculator.py:34`
**影响**: 所有使用 `_pct_rank` 的指标

```python
def _pct_rank(series, value):
    if series.empty or pd.isna(value):
        return np.nan
    return (series.dropna() < value).sum() / max(len(series.dropna()), 1)
# 当 series.dropna() 为空时，返回 0/max(0,1)=0.0
# 下游 _score_with_fallback(0.0) 返回 0.0 (有效值)，拉低维度平均分
```

**修复**:
```python
def _pct_rank(series, value):
    if series.empty or pd.isna(value):
        return np.nan
    clean = series.dropna()
    if clean.empty:
        return np.nan
    return (clean < value).sum() / len(clean)
```

---

### ~~BUG-3: fetch_all_history 中 margin/northbound 数据获取后未保存~~ ✅ 已修复

**文件**: `src/data/fetcher.py:452-453`
**影响**: `fetch_all_history()` 函数

```python
fetch_margin_history(start, end)      # 返回 DataFrame 但未调用 _save()
fetch_northbound_history(start, end)  # 返回 DataFrame 但未调用 _save()
# 数据从 tushare API 获取后被丢弃，从未写入数据库
```

**修复**:
```python
df = fetch_margin_history(start, end)
if df is not None and not df.empty:
    _save(df, "margin_history")

df = fetch_northbound_history(start, end)
if df is not None and not df.empty:
    _save(df, "northbound_history")
```

---

### ~~BUG-4: JSON 输出 NaN 值导致无效 JSON~~ ✅ 已修复

**文件**: `src/output/json_writer.py:55-57`
**影响**: `save_results()` 输出的 JSON 文件

```python
def _round_score(v):
    return round(float(v), 1) if v is not None else None
# v=float('nan') 时，None 棗查通过，round(float('nan'),1) 返回 NaN
# json.dump 输出 "NaN" (非标准 JSON)，JavaScript JSON.parse 会报错
```

**修复**:
```python
def _round_score(v):
    if v is None or (isinstance(v, float) and (pd.isna(v) or np.isinf(v))):
        return None
    return round(float(v), 1)
```

---

## P1 — 短期修复 (影响用户体验/稳定性)

### ~~BUG-5: 橙色等级显示绿色 emoji~~ ✅ 已修复

**文件**: `src/output/json_writer.py:222`
**影响**: 飞书通知文本

```python
f"综合热度：{'🔴' if level=='red' else '🟡' if level=='yellow' else '🟢'} {score:.0f}"
# level=='orange' 时，走到 else 分支显示 🟢
```

**修复**:
```python
EMOJI = {'red': '🔴', 'orange': '🟠', 'yellow': '🟡', 'green': '🟢'}
f"综合热度：{EMOJI.get(level, '⚪')} {score:.0f}"
```

---

### ~~BUG-6: analyze_state 在 history 缺少当天数据时计数错误~~ ✅ 已修复

**文件**: `src/output/json_writer.py:142-158`
**影响**: 防抖逻辑

```python
consecutive = 1
for h in reversed(history[:-1]):  # 总是丢弃最后一个元素
    if h.get("trade_date", "") >= current_date:
        continue
# 如果 history 中没有当天数据，history[:-1] 丢弃的是倒数第二天
# consecutive 计数少1，防抖可能提前或延迟触发
```

**修复**: 不要无条件丢弃最后一个元素，改为按日期过滤

---

### ~~BUG-7: HeatIndexCalculator._conn() 连接从未关闭~~ ✅ 已修复

**文件**: `src/indicators/calculator.py:94-99`
**影响**: 长时间运行时文件描述符泄漏

```python
def _conn(self):
    if not hasattr(self, "_db_conn") or self._db_conn is None:
        self._db_conn = sqlite3.connect(self.db_path)
    return self._db_conn
# 无 close() 方法，无 __del__，无上下文管理器
```

**修复**: 添加 close() 方法和上下文管理器

---

### ~~BUG-8: fetcher.py 多处裸连接异常时不关闭~~ ✅ 已修复

**文件**: `src/data/fetcher.py:104-106, 389-407`
**影响**: 数据库连接泄漏

```python
conn = sqlite3.connect(_db)
latest = conn.execute(...)  # 如果这里抛异常
conn.close()  # 这行不会执行
```

**修复**: 使用 try/finally 或上下文管理器

---

### ~~BUG-9: run_daily.py 回退结果缺少 dim_macro 字段~~ ✅ 已修复

**文件**: `scripts/run_daily.py:163-167`
**影响**: 计算失败时的回退数据不完整

```python
result = {
    "trade_date": trade_date, "composite_score": None,
    "dim_valuation": None, "dim_fund": None, "dim_sentiment": None,
    "dim_technical": None, "dim_structure": None, "indicators": {},
    # 缺少 "dim_macro": None
}
```

**修复**: 添加 `"dim_macro": None`

---

### ~~BUG-10: send_feishu_webhook 未捕获 JSON 解析异常~~ ✅ 已修复

**文件**: `src/output/json_writer.py:298-307`
**影响**: Webhook 返回非 JSON 时崩溃

```python
except (urllib.error.URLError, OSError) as e:
# json.JSONDecodeError 继承 ValueError，不在此列表中
```

**修复**: 添加 `json.JSONDecodeError` 到 except 列表

---

### ~~BUG-11: .env 解析器不处理引号包裹的值~~ ✅ 已修复

**文件**: `scripts/run_daily.py:22-25`
**影响**: TUSHARE_TOKEN 认证失败

```python
if line.startswith("TUSHARE_TOKEN="):
    os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1]
# TUSHARE_TOKEN="abc123" 会提取为 "abc123" (含引号)
```

**修复**: 添加 strip('"\'') 处理

---

## P2 — 中期改进 (代码质量/健壮性)

### ~~BUG-12: save_dataframe SQL 注入风险~~ ✅ 已修复

**文件**: `src/data/database.py:229`
**影响**: 安全性

```python
conn.execute(f'INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM _tmp_upsert')
# table 和 cols 直接拼接到 SQL 中，无白名单校验
```

**修复**: 添加表名白名单校验

---

### ~~BUG-13: _calc_deviation_ma250 除零风险~~ ✅ 已修复

**文件**: `src/indicators/calculator.py:708`
**影响**: 极端情况下 ZeroDivisionError

```python
deviation = (sh["close"].iloc[-1] / ma250.iloc[-1] - 1) * 100
# 如果 ma250.iloc[-1] == 0，抛出 ZeroDivisionError
```

**修复**: 添加零值检查

---

### ~~BUG-14: _series_pct_rank 与 _pct_rank 不一致~~ ✅ 已修复

**文件**: `src/indicators/calculator.py:34 vs 857`
**影响**: 估值计算与其他维度不一致

```python
# _pct_rank 用 < (严格小于)
return (series.dropna() < value).sum() / max(len(series.dropna()), 1)

# _series_pct_rank 用 <= (小于等于)
return (series <= value).sum() / len(series)
```

**修复**: 统一为一种比较方式

---

### ~~BUG-15: _calc_buffett_ratio 绕过 _score_with_fallback~~ ✅ 已修复

**文件**: `src/indicators/calculator.py:1035`
**影响**: NaN 可能泄漏到维度平均分

```python
return max(0, min(100, score))  # score=NaN 时返回 NaN
# 而 _score_with_fallback(NaN) 返回 None (被 _combine_dimension 过滤)
```

**修复**: 改用 `return _score_with_fallback(score)`

---

### ~~BUG-16: fetch_index_constituents 硬编码起始日期~~ ✅ 已修复

**文件**: `src/data/fetcher.py:126`
**影响**: 跨年后数据缺失

```python
df = pro.index_weight(index_code=ts_code,
                       start_date="20260101",  # 硬编码
                       end_date=today)
```

**修复**: 使用参数化的 start 日期

---

## P3 — 低优先级 (代码风格/兼容性)

### ~~BUG-17: config.py 使用 Python 3.10+ 语法~~ ✅ 已修复

**文件**: `src/config.py:14`
**影响**: Python 3.9 及以下无法运行

```python
def load_config(path: str | Path = None) -> dict:
# str | Path 语法需要 Python 3.10+
```

**修复**: 使用 `Optional[Union[str, Path]]`

---

### ~~BUG-18: run_daily.py 日志文件路径相对 CWD~~ ✅ 已修复

**文件**: `scripts/run_daily.py:42`
**影响**: 日志文件位置不可预测

```python
logging.FileHandler("run_daily.log", encoding="utf-8")
# 相对路径，取决于脚本运行时的 CWD
```

**修复**: 使用绝对路径，基于脚本位置

---

### ~~BUG-19: _calc_up_down_ratio 冗余的 min(score, 100)~~ ✅ 已修复

**文件**: `src/indicators/calculator.py:515`
**影响**: 与其他函数不一致

```python
score = min(score, 100)  # 冗余，_score_with_fallback 已处理
score = _score_with_fallback(score)
```

**修复**: 删除冗余的 min()

---

### ~~BUG-20: json_writer.py 裸 except 静默吞掉异常~~ ✅ 已修复

**文件**: `src/output/json_writer.py:87-88`
**影响**: 调试困难

```python
except Exception:
    pass  # history.json 损坏时静默跳过，无日志
```

**修复**: 添加 logger.warning

---

## 汇总

| 优先级 | 数量 | 关键问题 |
|--------|------|---------|
| **P0** | 4 | inf未过滤、pct_rank返回0、数据未保存、JSON无效 |
| **P1** | 7 | emoji错误、防抖计数、连接泄漏、回退缺字段 |
| **P2** | 5 | SQL注入、除零、不一致、硬编码 |
| **P3** | 4 | 版本兼容、日志路径、冗余代码、静默异常 |

---

*Bug 评估报告 v1.0 · 2026-06-14*
