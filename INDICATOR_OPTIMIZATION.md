# 指标体系优化计划 — P0~P3

> 基于 2026-06-14 指标评估报告，按紧急度和影响排序

---

## P0 — 立即修复 (本周)

### 1. ~~验证换手率计算~~ ✅ 已修复

**问题**: 换手率显示99.8%(极度活跃)，但涨跌家数比仅19.6%(普跌)，信号矛盾

**文件**: `src/indicators/calculator.py:434-474` (`_calc_turnover`)

**可能原因**: 
- amount单位千元 vs circ_mv单位万元的换算可能有误
- 预计算表 `daily_turnover` 仅1374天(2020-09起)，历史分位窗口不足

**验证方法**:
```python
# 对比原始数据
total_amount = stock_daily['amount'].sum()  # 单位千元
total_circ = stock_daily['circ_mv'].sum()   # 单位万元
turnover = total_amount / (total_circ * 1000) * 10  # 应该得到百分比
```

**改动**: 如果确认计算有误，修正换算公式

**理由**: 换手率是情绪维度的核心指标，计算错误会严重影响综合得分

---

### 2. ~~修复行业名称缺失~~ ✅ 已修复

**问题**: `sectors.json` 中 `industry` 字段为空字符串

**文件**: `src/indicators/sector_calculator.py:1341-1353`

**当前输出**:
```json
{"industry": "", "score": 82.5, ...}
```

**原因**: `sector_name` 使用 `SECTOR_NAME_MAP` 查找，但部分行业代码不在映射表中

**改动**: 在 `SECTOR_NAME_MAP` 中补充缺失的行业代码，或用 `stock_industry.code_name` 作为fallback

**理由**: 板块热度TOP10显示空行业名，用户体验差

---

### 3. ~~修复涨跌停比异常值~~ ✅ 已处理

**问题**: 当跌停数=0时，`limit_ratio = 涨停数/1` 可能产生极端值

**文件**: `src/indicators/calculator.py:565-589` (`_calc_limit_ratio`)

**改动**: 
```python
# 当跌停数=0时，limit_ratio封顶为涨停数本身，或设为NaN
if limit_down == 0:
    limit_ratio = min(limit_up, 10)  # 封顶10
else:
    limit_ratio = limit_up / limit_down
```

**理由**: 极端值会拉高情绪维度得分，产生误判

---

## P1 — 短期优化 (1-2周)

### 4. ~~增加得分平滑~~ ✅ 已实现

**问题**: 综合得分单日波动大(标准差12.1)，可能产生误信号

**文件**: `src/output/json_writer.py`

**方案**: 在 `save_results` 中增加3日移动平均
```python
# 读取最近3天历史，计算移动平均
if len(history) >= 2:
    recent_scores = [h['composite_score'] for h in history[-2:]] + [score]
    smoothed = np.mean([s for s in recent_scores if s is not None])
    result['composite_score_smoothed'] = round(smoothed, 1)
```

**输出**: 同时保存原始分数和光滑分数，前端显示光滑版本

**理由**: 减少单日噪声，更稳定的信号

---

### 5. ~~调整红区阈值~~ ✅ 已调整

**问题**: 红区触发率仅~5%，牛市顶漏判(2020/2024未触发)

**文件**: `src/output/json_writer.py:28-36`

**方案A**: 降低阈值
```python
def get_heat_level(score):
    if score >= 65:  # 原70→65
        return "red"
    elif score >= 40:
        return "yellow"
    else:
        return "green"
```

**方案B**: 增加橙色预警
```python
def get_heat_level(score):
    if score >= 70:
        return "red"      # 红色预警
    elif score >= 60:
        return "orange"   # 橙色关注 (新增)
    elif score >= 40:
        return "yellow"   # 黄色警惕
    else:
        return "green"    # 绿色安全
```

**理由**: 更及时的预警信号

---

### 6. ~~替换行业分化度~~ ✅ 已替换

**问题**: 行业分化度与综合得分相关性仅0.12(极低)，对综合得分贡献很小

**文件**: `src/indicators/calculator.py:738-775` (`_calc_sector_divergence`)

**方案**: 用创新高比例替代
```python
def _calc_new_high_ratio(self) -> Optional[float]:
    """创新高比例 — 更有效的结构指标"""
    # 统计收盘价≥250日最高价0.98的股票占比
    # 比行业分化度与综合得分相关性更高
```

**或者**: 降低结构维度权重(15%→10%)，减少噪音

**理由**: 低相关性指标会稀释其他维度的信号

---

### 7. ~~合并PE/PB为单一估值指标~~ ✅ 已合并

**问题**: PE分位和PB分位高度相关(r=0.85)，信息重叠

**文件**: `src/indicators/calculator.py:188-265`

**方案**: 
```python
def _calc_valuation_composite(self) -> Optional[float]:
    """估值复合指标 = PE分位×0.5 + PB分位×0.3 + 破净率反向×0.2"""
    pe = self._calc_pe_percentile()
    pb = self._calc_pb_percentile()
    bn = self._calc_below_net_rate()
    # 加权合成，减少维度冗余
```

**理由**: 减少信息重叠，估值维度更精简

---

## P2 — 中期改进 (1个月)

### 8. 增加恐慌指数

**问题**: 情绪维度缺少恐慌度量(当前仅用波动率替代)

**文件**: `src/indicators/calculator.py`

**方案**: 从akshare获取50ETF期权隐含波动率
```python
def _calc_vix_proxy(self) -> Optional[float]:
    """50ETF期权隐含波动率 — 恐慌指标"""
    try:
        import akshare as ak
        df = ak.option_50etf_qvix()
        # 取最近20日均值，与历史分位比较
    except:
        return None
```

**理由**: 恐慌指数是情绪维度的高价值指标

---

### 9. 调整维度权重

**问题**: 技术维度权重10%偏低，结构维度15%偏高(相关性低)

**文件**: `src/indicators/calculator.py:1077`

**当前**: `[0.25, 0.15, 0.15, 0.20, 0.10, 0.15]`

**建议**:
```python
# 方案A: 技术↑ 结构↓
weights = [0.25, 0.15, 0.15, 0.20, 0.15, 0.10]

# 方案B: 更激进
weights = [0.25, 0.15, 0.15, 0.20, 0.15, 0.10]
```

**理由**: 权重应与指标有效性(相关性/区分度)匹配

---

### 10. 增加多周期动量

**问题**: 当前仅60日动量，缺少短/中期趋势捕捉

**文件**: `src/indicators/calculator.py`

**方案**: 增加20日和120日动量
```python
def _calc_momentum_20d(self):
    """20日动量 — 短期趋势"""
    
def _calc_momentum_120d(self):
    """120日动量 — 中期趋势"""
```

技术维度从3项增至5项，权重可提升至15%

**理由**: 多周期动量能捕捉不同时间尺度的趋势

---

### 11. 优化变化率窗口

**问题**: 北向20日/两融3年窗口可能不够最优

**文件**: `src/indicators/calculator.py:360-432`

**方案**: 
- 北向: 20日→10日(更灵敏)
- 两融: 3年→2年(减少滞后)

**理由**: 窗口选择影响指标的灵敏度和稳定性

---

## P3 — 长期规划 (3个月+)

### 12. VIX数据源集成

**问题**: 波动率替代指标不够精确

**方案**: 
- 验证akshare `option_50etf_qvix` 接口
- 如果不可用，考虑用50ETF期权价格反推隐含波动率

**理由**: VIX是全球通用的恐慌指标

---

### 13. 全市场估值指标

**问题**: 当前估值仅用成分股口径(沪深300+中证500)，遗漏小盘股

**方案**: 增加全市场PE分位作为辅助指标

**理由**: 2015年牛市中小盘股估值泡沫更严重

---

### 14. 动态权重调整

**问题**: 固定权重无法适应不同市场环境

**方案**: 
- 牛市: 提升估值/情绪权重
- 熊市: 提升资金/技术权重
- 根据最近30天波动率自动调整

**理由**: 不同市场阶段的驱动因素不同

---

### 15. 回测自动化验证

**问题**: 每次指标调整后缺乏系统性回测验证

**方案**: 
- 在CI中添加回测步骤
- 自动对比新旧指标体系的关键节点得分
- 生成回测报告

**理由**: 确保每次改动不会降低指标质量

---

## 执行顺序建议

```
Week 1:  P0-1(换手率验证) + P0-2(行业名称) + P0-3(涨跌停异常值)
Week 2:  P1-4(得分平滑) + P1-5(红区阈值) + P1-6(替换行业分化度)
Week 3:  P1-7(合并PE/PB) + P2-9(调整权重)
Month 2: P2-8(恐慌指数) + P2-10(多周期动量) + P2-11(优化窗口)
Month 3: P3-12(VIX) + P3-13(全市场估值) + P3-15(回测自动化)
```

---

## 预期效果

如果执行P0+P1:
- 换手率信号矛盾 → 修正
- 行业名称显示 → 修复
- 涨跌停异常值 → 处理
- 得分波动降低 → 更稳定
- 红区触发率提升 → 更及时
- 结构维度有效性 → 提升

如果执行P0-P2:
- 综合得分相关性提升
- 指标体系更精简(减少冗余)
- 情绪维度增强(恐慌指数)
- 技术维度增强(多周期动量)

---

*指标优化计划 v1.0 · 2026-06-14*
