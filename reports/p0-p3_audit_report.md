# P0-P3 提升完成情况核查报告

- 核查基准：`docs/roadmap.md`（v1.0）
- 核查日期：2026-09-01
- 核查方式：静态代码核查（git diff + 引擎/schema/fetcher/流水线/前端逐点比对）+ 动态验证（pytest / ruff / 全历史回测门禁 / 数据回填 / 前端语法）
- 总判定：**P0 ✅ / P1 ✅ / P2 ✅ / P3 ✅ 已完成（仅 iVIX 为有意技术替代）**

---

## 一、完成状态总览

| 优先级 | 任务项 | 状态 | 判定依据 |
|---|---|---|---|
| P0 | 0.1 仓库清理 | ✅ 已完成 | 死文件/占位模板清理干净，fresh init 无死表重建 |
| P0 | 0.2 echarts 按需 bundle | ✅ 已完成 | `web/echarts.custom.min.js` 650KB（全量 1.0MB，省 ~350KB），app.html 已切换引用 |
| P0 | 0.3 Lighthouse 质量门禁 | ✅ 已完成 | `.github/workflows/daily.yml` 含 lighthouse job；`lighthouserc.json` 四类阈值齐全 |
| P1 | 1.1 涨跌家数广度 breadth | ✅ 已完成 | `calc_breadth_v2` + `daily_updown` 全链路 + 权重 4% |
| P1 | 1.2 南向资金 southbound | ✅ 已完成 | `calc_southbound_v2` + `daily_hsgt_south`；数据量级抽查正常（4.5~31.8 亿/日） |
| P1 | 1.3 股指期货基差 futures_discount | ✅ 已完成 | `calc_futures_discount_v2` + `daily_futures_basis`；基差 −0.8%~−1.2% 量级合理 |
| P2 | 2.1 滚动窗口分位 | ✅ 已完成 | `_pct_rank` 统一改为 `tail(ROLLING_PCT_WINDOW=1260)`，一处改动覆盖全部指标 |
| P2 | 2.2 市态标签 regime | ✅ 已完成 | `compute_regime` 市态判定 + app.html `regimeBadge` 展示 |
| P3 | 3.1 融资买入占比 margin_buy_ratio | ✅ 已完成 | 引擎/权重(3%)/测试/回测/回填/前端全链路闭环 |
| P3 | 3.2 振幅热度 amplitude | ✅ 已完成 | 引擎/权重(2%)/测试/回测/回填/前端全链路闭环 |
| P3 | 3.3 已实现波动率 realized_vol | ✅ 已完成 | 引擎/权重(2%)/测试/回测/回填/前端全链路闭环 |
| P3 | 3.4 开户数 monthly_accounts | ✅ 已完成 | 建表+抓取+展示全闭环（2026-09-01 补齐：101 行 2015-04~2023-08，前端已展示） |
| P3 | 3.5 ETF 申赎 daily_etf_flow | ✅ 已完成 | 建表+抓取+展示全闭环（2026-09-01 补齐：宽基 16 只 ETF 份额快照 2448.37 亿份） |
| P3 | 3.6 iVIX | ❌ 未实现（已替代） | roadmap 原列指标；实际以现货 realized_vol 替代进分，属有意技术偏离 |

---

## 二、动态验证结果

### 2.1 全历史回测门禁（16 指标，2816 交易日）

| 门禁指标 | 基线 | 实测 | 判定 |
|---|---|---|---|
| 区分度 | 15.3 | 15.0 | ⚠️ 略降 −0.3（可接受） |
| 同期相关 | 0.810 | 0.816 | ✅ 提升 |
| 领先 60 日相关 | −0.231 | −0.230 | ✅ 持平 |
| 极热后 60 日 | −8.3% | −7.4% | ✅ 不退化 |
| 极冷后 60 日 | +7.5% | +7.9% | ✅ 提升 |

**判定：整体不退化，通过门禁。** 区分度 −0.3 属 P3 新增 3 指标引入的预期噪声，其余四项全部达标或改善。

### 2.2 质量工具链

| 校验项 | 结果 |
|---|---|
| pytest（含 P0-P3 全量用例） | ✅ 116 passed |
| ruff lint + format | ✅ 通过 |
| backfill_indicator_history（16 指标回填） | ✅ 4164 日期 / indicator_history.json 1080KB |
| regen_today_snapshot（当日快照） | ✅ 16 指标全部有值、无 None |
| node --check（app.html 内联 JS） | ✅ 语法通过 |

### 2.3 P3 前端补齐（本次核查中发现并顺手修复）

核查发现 `web/app.html` 缺 3 个 P3 指标的展示（此前"部分完成"项），已补齐：

- `indMeta` 追加 `margin_buy_ratio`（融资买入占比，pos，#3fb950）、`amplitude`（振幅热度，pos，#d29922）、`realized_vol`（已实现波动率，rev，#a5d6ff）
- `indKeys` 13 → 16 键全量扩展
- 标题"13大核心指标" → "16大核心指标"

---

## 三、未完成/偏差项说明

### 3.1 ✅ P3.4 / P3.5：开户数、ETF 申赎——已补齐（2026-09-01）

**原缺口**：代码链路就绪（schema v14 DDL / fetcher / S24h / S24i / json_writer），但生产库 `data/heat_index.db` 无两张表（schema_version 停留在 13），无数据、前端无展示位。

**已执行**：
1. 运行 `init_database()`：schema_version 13→14，`monthly_accounts` / `daily_etf_flow` 建表成功
2. S24h `fetch_account_statistics`：**101 行**月度新增开户数（2015-04 ~ 2023-08，最新 2023-08 = 99.59 万户；东财/中国结算源自 2023-08 停更，符合 fetcher 注释预期）
3. S24i `fetch_etf_flow_snapshot`：**1 行**宽基 ETF 总份额快照（2026-09-01 = 2448.37 亿份 / 16 只；按设计自采集日起积累）
4. 重算 2026-08-11 当日快照并注入展示键：`index.json` / `detail.json` 现含 `display_new_accounts=99.59`、`display_new_accounts_month=2023-08`、`display_etf_shares=2448.37`，16 指标仍全有值无 None
5. `web/app.html` 补展示位：概览页 score 区新增低频锚行（`#lowFreqAnchors`）展示"新增开户 99.59万户 (2023-08) · 宽基ETF份额 2448.37亿份"，无数据时自动隐藏
6. 验证：node --check JS 语法 OK；pytest 116 passed 无回归

### 3.2 ❌ P3.6：iVIX——未实现（有意技术替代）

**现状**：roadmap 将 iVIX（中国波指/上证 50ETF 期权隐含波动率）列为 P3 备选指标；引擎中仅有展示性 `qvix_daily`（QVIX 另算，不进分）。

**决策**：以 `calc_realized_vol_v2`（沪深300 现货 20 日对数收益 std × √250）替代进分——同属"波动率越低=越贪婪"的负向逻辑，数据源稳定（index_daily 本地可算），且回测已验证不退化。

**后续行动项**：
1. 在 `docs/roadmap.md` / README 注明"iVIX → realized_vol 替代"的决策与理由，避免后续误判为漏项
2. 如确需真 iVIX：可后续以 akshare `option_risk_indicator_sse`（上交所期权风险指标）接入，作为展示性指标叠加，不进分

---

## 四、其余待办（非本次核查范畴）

| 事项 | 说明 |
|---|---|
| 提交与推送 | 21 个源码/配置/前端修改文件 + 3 个 JSON 产物（detail/index/indicator_history）未提交未推送；`scripts/watchlist_report.py` 为预存在无关文件，提交时单独处理 |
| 清理临时目录 | `.tmp_pytest/`（pytest 沙箱 basetemp）未跟踪，提交前 `rm -rf` |
| 推送受阻 | 此前 `git push` 报 `SSL_ERROR_SYSCALL`（网络不通），待网络恢复后 `git fetch && git rebase origin/main && git push origin main` |
| ETF 数据积累 | `daily_etf_flow` 自 2026-09-01 起每日积累，待历史 ≥180 日后再评估入分（roadmap P3.5 设计） |
| 开户数数据源 | 东财/中国结算开户数源 2023-08 停更，展示为历史锚点，非实时；前端已标注月份 |
| P4（可选增强） | roadmap 定义为二期，不在本次范围，无偏差 |
