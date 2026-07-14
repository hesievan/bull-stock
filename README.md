# A股牛市热度指数

> 每日更新的量化工具，从 **4 个维度、9 个核心指标 + CFFEX 股指期权恐慌指数** 综合评估 A 股市场整体热度水平，
> 并对 **沪深 300 / 创业板 / 科创 50 / 北证 50 / 中证 A500 / 中证 1000 / 中证红利** 七大核心指数
> 单独输出牛市见顶预判信号。
>
> **定位：仅提示离场 / 减仓，不发出进场或加仓信号。**

<p align="center">
  <img src="https://img.shields.io/badge/version-v3.17-blue" alt="version">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/tests-70_passing-brightgreen" alt="tests">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
</p>

---

## 目录

- [热度区间](#热度区间)
- [回测验证](#回测验证)
- [指标体系](#指标体系)
- [快速开始](#快速开始)
- [每日流水线](#每日流水线)
- [查看结果](#查看结果)
- [API 接口](#api-接口)
- [项目结构](#项目结构)
- [技术栈](#技术栈)

---

## 热度区间

| 颜色 | 分数 | 含义 | 行动建议 |
|------|------|------|---------|
| 🟢 绿色安全 | 0–40 | 估值合理/偏低，情绪冷淡 | 安全区间 |
| 🟡 黄色警惕 | 40–55 | 部分指标偏高 | 需关注 |
| 🟠 橙色关注 | 55–65 | 多项指标偏高 | 考虑减仓 |
| 🔴 红色预警 | 65–100 | 多项指标历史高位 | 考虑离场 |

---

## 回测验证（V2 引擎，2026-06 基线重算）

| 日期 | 市场状态 | V2 综合热度 | 信号 |
|------|---------|------------|------|
| 2015-06-12 | 牛市顶 | **83.8** 🔴 | 正确触发红区，信号极强 |
| 2018-12-28 | 熊底 | **5.2** 🟢 | 正确触发绿区，极度低估 |
| 2020-07-10 | 牛市启动 | **70.4** 🔴 | 正确提前预警红区 |
| 2021-02-18 | 牛市顶 | **74.1** 🔴 | ✅ 正确触发红区（V1 仅橙区，V2 修正） |
| 2024-02-05 | 熊底 | **23.5** 🟢 | 正确触发绿区 |
| 2024-10-08 | 脉冲顶 | **49.1** 🟡 | 正确识别为脉冲，非真顶 |
| 2026-06-24 | 震荡市 | **53.9** 🟡 | 黄色警惕 |
| 2026-06-25 | 震荡市 | **54.6** 🟡 | 黄色警惕 |

### 指数牛市见顶预判

| 指数 | 技术指标 | 估值指标 | 评分原理 |
|------|---------|---------|---------|
| 沪深 300 | MA 偏离 + 20/60/120 动量 | PE+PB 分位 | 50% 技术 + 50% 估值 |
| 创业板指 | MA 偏离 + 20/60/120 动量 | PE+PB 分位 | 50% 技术 + 50% 估值 |
| 科创 50 | MA 偏离 + 20/60/120 动量 | — | 纯技术评分 |
| 北证 50 | MA 偏离 + 相对强弱 vs 沪深300 | — | 纯技术评分(相对强弱) |
| 中证 A500 | MA 偏离 + 相对强弱 vs 沪深300 | — | 纯技术评分(相对强弱) |
| 中证 1000 | MA 偏离 + 20/60/120 动量 | — | 纯技术评分 |
| 中证红利 | MA 偏离 + 20/60/120 动量 | PE+PB 分位 | 50% 技术 + 50% 估值 |

> 沪深 300 额外展示**市赚率 PR**（PR = PE / ROE = PE² / PB），仅展示不参与评分。
> 量能评分仅作参考（tushare 量价数据近年单位不统一），不参与综合计分。

---

## 指标体系

### V2 引擎（每日流水线所用）

| 维度 | 权重 | 子指标 | 说明 |
|------|------|-------|------|
| **估值** | 40% | PE 分位、ERP、巴菲特指标 | 估值水位，越高越贵 |
| **资金** | 30% | 两融余额市值比、存款市值比(M2/总市值) | 杠杆水位 + 资金搬家 |
| **情绪** | 20% | 成交额 M2 比、换手率 | 市场活跃度 |
| **结构** | 10% | 创新高占比、MA 排列比 | 内部结构健康度 |

**展示不计分**: 恐慌指数（CFFEX 股指期权隐含波动率）

> 恐慌指数 (Panic Index) = **0.3 × 上证50隐波 + 0.4 × 沪深300隐波 + 0.3 × 中证1000隐波**
> 数据源: [optbbs.com](http://1.optbbs.com/d/csv/d/k.csv) — 中金所 CFFEX 股指期权 QVIX
> 计算: 取 HO (上证50) / IO (沪深300) / MO (中证1000) 三种股指期权的隐含波动率，加权合成
> 集中度 = 中证1000隐波 - 上证50隐波（衡量小盘 vs 大盘的波动溢价）
>
> 历史极值参考: 2024-02 微盘危机恐慌指数 26.3（橙区），2024-10 政策脉冲 41.7（红区）
> 正常区间: 绿 &lt;20 / 黄 20-25 / 橙 25-30 / 红 &gt;30

### 评分合成路径

```
原始数据 → 10年历史百分位 (0-100)
        → 指标加权合成综合分 → 3 日平滑 → 四区间分类
```

### 背离惩罚

- **情绪背离**: 高换手率 + 指数下跌 → 情绪得分扣减最多 20 分
- **新高顶背离**: 指数涨 + 新高占比下降且 < 30% → 结构分扣减最多 15 分

---



## 快速开始

### 前置条件

- Python 3.10+
- tushare 账号（2000+ 积分）
- SQLite（系统自带）

### 安装

```bash
git clone <repo_url>
cd bull-market-heat-index
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入你的 TUSHARE_TOKEN
```

### 运行

```bash
# 今日计算
python scripts/run_daily.py

# 指定日期
python scripts/run_daily.py 2026-06-18

# 运行测试
python -m pytest tests/ -v

# 启动 HTTP 服务（静态文件）
cd web && python3 -m http.server 8080
```

> 详细操作指南请参见 [`使用指南.md`](使用指南.md)。

---

## 每日流水线

所有步骤独立 try/except，单步失败不阻断后续流程。

| Step | 名称 | 说明 | 耗时 |
|------|------|------|------|
| S0 | init_db | 数据库建表/迁移 | <0.1s |
| S1 | S1_index | 7 指数日行情增量 | 1–5s |
| S2 | S2_market | 全市场日K线 + PE/PB/市值 | 3–8s |
| S25 | S25_index_pe | 成分股 PE/PB 中位数(供 ERP) | 0.5–2s |
| S26b | S26b_total_mv | 全市场总市值(供巴菲特指标) | 0.5–2s |
| S26 | S26_circ_mv | 全市场流通市值(供融资余额比) | 0.5–2s |
| S27–S30 | updown / limit / below_net / ma_alignment | 展示用预计算表（不计分） | 各 0.5–3s |
| S24 | S24_precompute | 预计算表陈旧检测 | <0.1s |
| S24c | S24c_m2 | M2 月度数据 | <0.3s |
| S31 | S31_qvix | CFFEX 股指期权恐慌指数更新（optbbs） | 1–5s |
| S3 | margin / bond_yield | 融资融券 + 国债收益率（akshare） | 0.1–0.5s |
| **S5** | **S5_calc** | **V2 引擎：9 指标 + 恐慌指数 + 维度加权合成** | **5–15s** |
| **S55** | **S55_index_heat** | **七大指数牛市见顶预判** | **<0.1s** |
| S6 | save | 保存 JSON（含展示指标） | <0.1s |
| S7 | sectors | 板块热度 | 1–7s |
| S8 | final_save | 最终保存 | <0.1s |
| S9 | notify | 飞书 / Bark 推送 | <0.5s |

---

## 查看结果

所有计算结果输出到 `web/data/` 目录：

| 文件 | 说明 |
|------|------|
| `index.json` | 最新热度（综合分 + 4 维度分 + 9 指标原始值） |
| `detail.json` | 含完整指标明细（9 指标百分位评分 + 原始值 + 恐慌指数成分分解） |
| `history.json` | 历史热度序列 |
| `indicator_history.json` | 9 指标历史趋势 |
| `index_heat.json` | 七大指数最新过热评分 |
| `index_heat_history.json` | 七大指数历史评分时序 |
| `sectors.json` | 板块热度排名 |
| `run_status.json` | 各 Step 执行状态 |

```bash
cat web/data/index.json
```

### 前端页面

仪表盘 SPA 包含三个标签页：

- **概览**：综合热度仪表盘（仪表盘 + 历史走势 + 七大指数热度卡片，含评分折线图，按红/黄/绿分段着色）
- **明细**：9 个核心指标历史趋势图（含牛熊均线参考线和极值标记线）+ 恐慌指数卡片（含公式说明、三品种成分值、集中度）
- **记录**：近期每日热度得分明细表

```bash
cd web && python3 -m http.server 8080
# 浏览器访问 http://127.0.0.1:8080/app.html
```

---

## API 接口

| 接口 | 说明 | 参数 |
|------|------|------|
| `GET /api/heat` | 最新热度指数 | — |
| `GET /api/history` | 历史数据 | `days`（默认 30） |
| `GET /api/sectors` | 板块热度 | — |
| `GET /api/detail` | 详细指标拆解 | — |
| `GET /api/strategy` | 策略信号 | — |
| `GET /api/health` | 健康检查 | — |

```bash
python scripts/api_server.py
curl http://localhost:8000/api/heat
curl http://localhost:8000/api/strategy
```

---

## 项目结构

```
bull-market-heat-index/
├── src/
│   ├── config.py                    # YAML 配置加载
│   ├── data/
│   │   ├── database.py              # SQLite 管理（25 表、迁移、CRUD）
│   │   ├── fetcher.py               # tushare + akshare 数据获取
│   │   ├── qvix_fetcher.py          # CFFEX 股指期权恐慌指数（optbbs CSV）⚠️ 新增
│   │   └── freshness.py             # 数据新鲜度与权重衰减（V1）
│   ├── indicators/
│   │   ├── heat_index_v2.py         # ⭐ V2 引擎 — 每日流水线所用（4 维度 9 指标）
│   │   ├── calculator.py            # V1 引擎（19 指标 / 6 维度，保留供参考）
│   │   ├── utils.py                 # 共享工具
│   │   ├── valuation.py             # V1 估值维度
│   │   ├── macro.py                 # V1 宏观维度
│   │   ├── fund.py                  # V1 资金维度
│   │   ├── sentiment.py             # V1 情绪维度
│   │   ├── technical.py             # V1 技术维度
│   │   ├── structure.py             # V1 结构维度
│   │   ├── index_heat.py            # 七大指数过热预判
│   │   └── sector_calculator.py     # 板块热度
│   └── output/
│       └── json_writer.py           # JSON 输出 + 飞书 / Bark 通知
├── scripts/                         # 流水线 + 工具脚本
│   ├── run_daily.py                 # ⭐ 每日流水线入口
│   ├── gen_report.py                # 日报生成（MD / HTML / PNG）
│   ├── api_server.py                # FastAPI REST API
│   ├── backfill_index_heat_history.py # 指数热度历史批量回填
│   ├── backfill_precompute.py       # V2 预计算表历史回填（CI 种子库构建）
│   ├── db_maintenance.py            # 数据库维护
│   ├── db_compress.py               # 备份 / 恢复 / 种子库压缩
│   └── ...                          # 回测 / 分析工具
├── tests/                           # 70 个单元测试
├── config/                          # dev.yaml / prod.yaml
├── web/                             # 前端 SPA（ECharts 暗色主题）
│   ├── app.html                     # 主仪表盘
│   ├── echarts.min.js               # 本地 ECharts
│   └── data/                        # JSON 输出
├── reports/                         # 日报 / 回测报告
├── data/                            # SQLite 数据库（~600MB，gitignore）
├── logs/                            # 运行日志
├── .github/workflows/               # CI/CD 流水线
├── requirements.txt                 # 生产依赖
├── requirements-dev.txt             # 开发依赖
└── 使用指南.md                       # 详细操作文档
```

---

## 技术栈

| 层 | 技术 |
|----|------|
| 语言 | Python 3.10+ |
| 数据存储 | SQLite（25 表，WAL 模式，~600MB） |
| 数据源 | tushare pro + akshare（国债收益率经 akshare）+ optbbs.com（CFFEX QVIX） |
| 核心库 | pandas, numpy, pyyaml |
| API 服务 | FastAPI + uvicorn |
| 前端 | Vanilla JS + ECharts（暗色 SPA，响应式） |
| CI/CD | GitHub Actions（每日 16:30 北京时） |
| 通知 | 飞书 Webhook + Bark（iOS 推送） |
| 测试 | pytest（70 例） + ruff |

---

## 开发

```bash
# 安装开发依赖
pip install -r requirements-dev.txt

# 代码检查
ruff check src/ scripts/ --select E,F,W
ruff format --check src/ scripts/

# 运行测试
python -m pytest tests/ -v
```

---

## 许可证

MIT

---

*版本: v3.18 | 调整: 2026-07-14 | 新增: 中证红利指数过热预判 + 沪深300市赚率指标*
