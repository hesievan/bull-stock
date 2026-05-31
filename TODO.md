# A股牛市热度指数 — 维护手册

> 更新: 2026-06-01 | 项目状态: Phase 1 MVP 冲刺 (目标 6/10 上线)
> 全市场数据扩展完成: stock_daily 从 260 只 → 5500 只

---

## 📊 当前状态速览

| 维度 | 状态 | 说明 |
|------|------|------|
| 数据层 | ✅ 基本就绪 | 全市场 PE/PB/市值回填中 (2769天) |
| 计算引擎 | ✅ 可用 | 20 子指标全部输出真实数据 |
| 自动化 | ✅ cron 已配 | 每交易日 16:30 触发 |
| 飞书通知 | ⚠️ 待验证 | 防抖逻辑已实现，Webhook 待配 |
| 前端 | ⚠️ 待联调 | 页面可用，需验证全市场数据渲染 |
| GitHub | ❌ 未推送 | 需创建仓库 |

---

## 🔴 上线前必做 (6/10 前)

### 数据完整性
- [ ] 确认全市场 PE/PB 回填完成 (检查 `data/backfill_full_market.log`)
- [ ] 验证回填后各日期股票数: 2015年应≥2000只, 2024年应≥5000只
- [ ] 跑一次完整回测 (`scripts/backtest.py`) 验证全市场数据下的指标表现
- [ ] 检查 stock_daily 中 peTTM/pbMRQ/pct_change 有效率 (目标: >90%)

### 端到端验证
- [ ] `run_daily.py` 对最新交易日跑通 (含全市场 PE/PB 更新)
- [ ] 检查 web/data/index.json 字段完整性 (composite + 5维度 + 20子指标)
- [ ] 前端页面渲染验证 (仪表盘 / 雷达图 / 历史走势)

### 自动化验证
- [ ] 手动触发 copaw cron: `copaw cron run 63be5c6c` → 飞书收到通知
- [ ] 验证红区/绿区通知逻辑 (可临时调阈值测试)

### GitHub
- [ ] 创建 GitHub 仓库 `bull-market-heat-index`
- [ ] `git push` 代码
- [ ] 验证 GitHub Actions 触发

---

## 🟡 指标质量优化 (按评估报告)

### 必须修复 (影响准确性)

| # | 问题 | 影响 | 工作量 | 建议 |
|---|------|------|--------|------|
| 1 | **2015 牛市顶未触发红区** | 最大漏洞 | 中 | 补充 2010-2018 融资融券历史 (akshare 有更长历史) |
| 2 | **创新高占比永远为 0** | 指标失效 | 小 | 改为"距新高 5% 以内比例"或直接移除 |
| 3 | **量价背离仅 3 档** (35/50/65) | 无区分度 | 中 | 改为连续评分 (10日量价相关系数的10年分位) |
| 4 | **行业分化度仅 4 档** (20/40/60/80) | 粒度粗 | 小 | 改为连续分位 (10年std分位) |

### 建议优化 (提升精度)

| # | 问题 | 影响 | 工作量 | 建议 |
|---|------|------|--------|------|
| 5 | 北向资金仅用天数占比 | 丢失金额量级 | 小 | 改为"净流入金额的10年分位" |
| 6 | bond_yield 2018前缺失 | ERP/股债比用默认值 | 中 | 用 akshare `bond_china_yield` 补充 2010-2018 |
| 7 | 仅 baostock 成分股有 K 线 | up_down_ratio 仅覆盖部分股票 | 大 | daily 已有 pct_chg，可扩展涨跌家数比到全市场 |
| 8 | 红区阈值 70 可能过高 | 2015/2021 顶部漏报 | 小 | 考虑降至 65，或按全市场数据重新校准 |

### 建议新增指标

| 指标 | 数据源 | 理由 |
|------|--------|------|
| 两融余额/流通市值比 | tushare margin | 比单一融资买入占比更全面 |
| IPO 破发率 | tushare new_share | 市场情绪的领先指标 |
| ETF 资金净流入 | akshare | 机构情绪指标 |

---

## 🟢 日常维护

### 每日 (自动)
- `copaw cron` 每交易日 16:30 自动触发 `run_daily.py`
- 自动拉取: baostock 指数+成分股K线 → tushare 全市场 PE/PB → 融资融券/北向/国债
- 自动计算: 20 子指标 → 综合热度 → JSON 输出
- 自动通知: 红区(≥70) → 飞书推送 (含防抖)

### 每周检查
- [ ] 查看 `run_daily.log` 有无 ERROR
- [ ] 检查 stock_daily 最新日期的股票数 (应 ≥ 5000)
- [ ] 检查 tushare API 调用是否触发频率限制

### 每月检查
- [ ] 更新新增投资者数据 (`scripts/import_investors.py`)
- [ ] 检查 M2 月度数据是否更新 (akshare `macro_china_money_supply`)
- [ ] 清理 `web/data/*.json` 旧文件 (保留近 90 天)
- [ ] SQLite 数据库大小检查 (正常应在 500MB 以内)

### 每季度检查
- [ ] 重新跑回测 (`scripts/backtest.py`) 验证指标稳定性
- [ ] 检查 tushare 积分是否过期/变动
- [ ] 检查 baostock 接口是否有变动
- [ ] 更新 `README.md` 数据完整性快照表

---

## 🔧 故障排查

### 常见问题

| 症状 | 原因 | 解决 |
|------|------|------|
| `run_daily.py` 卡住不动 | baostock 登录超时 | `bs_logout()` 后重试，或重启进程 |
| tushare 报"频率超限" | 200次/分钟限制 | 等 1-2 分钟自动恢复；backfill 脚本已内置 sleep |
| 综合热度突然归零 | 某维度计算异常返回 None | 检查 `run_daily.log` 中的 ERROR |
| 飞书通知未收到 | Webhook URL 失效 | 检查 `json_writer.py` 中的 WEBHOOK_URL |
| stock_daily 股票数骤降 | tushare daily_basic 某日无数据 | 检查 `backfill_full_market.log` 中对应日期 |
| 前端不显示数据 | JSON 文件未生成 | 手动跑 `run_daily.py` 检查输出 |

### 数据修复

```bash
# 修复单日数据
python scripts/run_daily.py 2026-05-29

# 重新回填全市场 PE/PB (从指定日期)
python scripts/backfill_full_market_pe.py 2026-05-01

# 重新拉取 baostock 历史
python scripts/init_history.py 2015-01-01

# 重建 market_cap 表
python -c "
from src.data.fetcher import rebuild_market_cap
rebuild_market_cap()
"
```

---

## 📈 全市场数据扩展后的影响

### 数据量变化

| 表 | 扩展前 | 扩展后 | 变化 |
|----|--------|--------|------|
| stock_daily (每日股票数) | ~260 | ~5,200 | **20x** |
| stock_daily (总行数) | 580,437 | ~14,000,000 | **24x** |
| stock_daily PE 有效率 | 92% | >95% | 提升 |
| stock_daily pct_change 有效率 | ~20% | >95% | **大幅提升** |

### 指标变化 (2026-05-29)

| 指标 | 成分股 | 全市场 | 解读 |
|------|--------|--------|------|
| PE 分位 | 21 | **100** | 小盘股 PE 远高于大盘股 |
| 估值维度 | 27.9 | **65.7** | 全市场估值被低估 |
| 涨跌家数比 | 52.3 | **19.1** | 大盘涨但多数小盘跌 |
| 年线上方比 | 42% | **3.4%** | 市场广度极差 |
| 综合热度 | 49.5 | **52.4** | 估值拉高但情绪/技术拉低 |

### 注意事项
- **数据库体积增大**: 约 500MB → 2-3GB，注意磁盘空间
- **计算时间增加**: 全市场 PE 中位数计算约增加 2-3 秒
- **回填耗时**: 2769 天 × 2 API 调用/天 ≈ 45-70 分钟
- **tushare 频率**: daily_basic 200次/分钟，backfill 已内置 sleep

---

## 🗂️ 项目结构

```
bull-market-heat-index/
├── src/
│   ├── data/
│   │   ├── database.py      # 16张表, turnover_rate列
│   │   └── fetcher.py       # 三源合一 + fetch_daily_basic_to_stock_daily()
│   ├── indicators/
│   │   └── calculator.py    # 20子指标, 单位已修正
│   └── output/
│       └── json_writer.py   # JSON + 飞书通知 + 防抖
├── scripts/
│   ├── run_daily.py         # 每日入口 (7步)
│   ├── backfill_full_market_pe.py  # 全市场PE/PB回填
│   ├── backtest.py          # 历史回测
│   ├── init_history.py      # baostock历史初始化
│   ├── import_investors.py  # 新增投资者录入
│   └── step1~5b             # tushare分步拉取
├── web/
│   ├── index.html           # 前端 (深色主题+ECharts)
│   └── data/                # JSON输出
├── data/
│   ├── heat_index.db        # SQLite (~2-3GB)
│   └── backfill_full_market.log  # 回填日志
└── .github/workflows/
    └── daily.yml            # GitHub Actions
```

---

## 📋 关键配置

| 配置 | 值 | 位置 |
|------|-----|------|
| 项目路径 | `/Users/hesi/bull-market-heat-index` | — |
| 数据库 | `data/heat_index.db` | 项目内 |
| tushare token | `473bc9...b389577` | `~/daily_stock_analysis/.env` |
| copaw cron ID | `63be5c6c-210f-4e2c-8393-414f41e97b3a` | copaw cron list |
| 飞书 Webhook | stock-monitor 旧 Webhook | `json_writer.py` |
| 红区阈值 | ≥ 70 | `json_writer.py` DEBOUNCE_RED_DAYS=2 |
| 回测区间 | 2015-01-01 ~ 今 | `backtest.py` |

---

## 📝 维护日志

| 日期 | 操作 | 备注 |
|------|------|------|
| 2026-06-01 | 全市场 PE/PB 扩展 (260→5500只) | 回填 2769 天中 |
| 2026-06-01 | 评估报告完成 | 综合评分 3.6/5 |
| 2026-05-31 | tushare 2000积分全量数据拉取 | 北向/国债/daily_basic |
| 2026-05-30 | 巴菲特指标 + 沪深300股债比上线 | 估值维度 4→6 指标 |
| 2026-05-29 | 项目骨架创建 | 三源合一架构 |

---

## 🎯 里程碑

| 日期 | 目标 | 状态 |
|------|------|------|
| 6/10 | MVP 上线: 飞书通知 + copaw cron + 全市场数据 | 🔄 |
| 6/24 | Phase 2: 行业热度 + 板块热力图 | ⏳ |
| 7/15 | 指标优化: 修复评估报告中的 4 个必须修复项 | ⏳ |
| 8/01 | v1.0.0: 单元测试 + GitHub Actions + 文档完善 | ⏳ |
