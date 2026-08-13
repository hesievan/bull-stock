# 牛市热度指数项目代码审查报告 (2026-08-13)

> 审查日期：2026-08-13  
> 审查范围：整个项目（src/、scripts/、tests/、web/、docs/、README.md 等）  
> 版本：v3.24（V2 引擎 + 11 指标 + 4 维度体系）

---

## 一、项目概览

本项目是一个 **A股牛市热度指数** 量化分析工具，每天计算综合热度指数（0-100），并输出到静态 Web 前端。核心定位是仅提供离场/减仓提示，不发出进场信号。

**V2 引擎核心**：11 个核心指标（估值、资金、情绪、结构）+ 恐慌指数（不计分），采用历史百分位法 + 权重合成 + 背离惩罚。

**当前状态**：基于 2026-08-09 审查报告中的问题已全部修复（创新高百分位、情绪背离扣分、换手率口径、预计算表等）。回测验证完整（2816 个交易日），统计指标稳定。

---

## 二、代码质量评估

### 优点（Strengths）
- **结构清晰**：src/ 模块化（data/、indicators/、output/），scripts/ 职责单一，tests/ 单元测试完善。
- **数据处理成熟**：SQLite + 预计算表 + 迁移系统，DB 管理高效（25 表，~1.7GB）。
- **日志与监控**：合理使用 logger，错误处理有重试和 fallback。
- **前端友好**：静态 HTML + ECharts，响应式 SPA，数据分离。
- **测试覆盖**：82+ 测试，覆盖 config、database、indicators、json_writer 等。
- **文档丰富**：README、多个 report/ 和 docs/ MD 文件，覆盖回测、指标、流水线。

### 缺点 / 潜在问题（Weaknesses）
- **类型提示缺失**：大部分 src/ 文件缺少 type hints，影响可读性和 IDE 支持。
- **部分代码冗余注释**：部分文件存在历史权重调整注释（如 heat_index_v2.py），建议清理。
- **生产环境日志**：部分 logger.warning/error 消息过长或包含敏感数据，建议结构化日志（JSON）。
- **依赖管理**：requirements.txt 和 requirements-dev.txt 较长，建议 pin 版本或使用 pyproject.toml。
- **前端轻量**：纯 vanilla JS，缺乏单元测试或端到端测试。
- **CI/CD 缺失细节**：GitHub Actions 存在但未显示完整覆盖率或 lint 检查。
- **性能边界**：10 年回测在 CI 环境可能耗时较长（虽已预计算）。

---

## 三、主要优化建议

### 1. 代码规范与类型化（高优先级，1-2 天）
- 在所有 Python 文件中添加类型提示（使用 `typing`、`dataclasses`）。
- 运行 ruff + black 格式化并修复剩余问题。
- 移除 heat_index_v2.py 等文件中的历史注释残留。

### 2. 性能与可扩展性
- 在 run_daily.py 和 backfill_*.py 中添加详细耗时日志 + 进度条。
- 对 daily_new_high、daily_turnover 等大表查询添加索引。
- 考虑将部分计算转为 pandas 矢量化或 NumPy 加速。
- 添加数据库定期 vacuum/analyze 命令到 daily pipeline。

### 3. 测试完善
- 为 focus_industries.py、sector_calculator.py、qvix_fetcher.py 补充单元测试。
- 添加集成测试（e.g. run_daily.py 端到端）。
- 在 CI 中启用 pytest-cov 并上传覆盖率报告。

### 4. 配置与依赖
- 使用 pydantic v2 为 config.yaml 提供 Pydantic 模型（weights、thresholds 等）。
- 更新 requirements.txt 使用 pinned 版本 + requirements-dev.txt 补充 dev 工具（ruff, pytest-cov, black）。
- 支持 .env 更安全地加载 TUSHARE_TOKEN。

### 5. 前端与用户体验
- 为 app.html 添加 JS 单元测试（或 Cypress）。
- 增加交互功能：如可切换指标对比、导出 CSV、深色/浅色主题切换。
- 优化 ECharts 图表加载（懒加载大图表）。

### 6. 日志与监控
- 统一使用 structlog 或 Python logging 的 JSON 格式。
- 在 run_status.json 中添加更多元数据（内存使用、DB 版本）。
- 添加健康检查端点到 api_server.py。

### 7. 工程化提升
- 提取 common utils 到 src/utils.py（目前分散在 indicators/）。
- 添加 type guard 和 runtime validation。
- 考虑使用 Poetry 或 Hatchling 替代 pip 管理。
- 定期清理 data/ 中的备份文件（现有 scripts/db_tools.py 可强化）。

### 8. 其他建议
- 监控每日运行日志中的异常率。
- 考虑添加 Watchlist 监控功能到 API。
- 更新 README.md 和 使用指南.md 包含最新优化项和运行示例。

---

## 四、结论与行动计划

当前代码质量在量化工具中属于 **良好水平**，已具备日常使用能力。**核心价值**在于 V2 引擎的指标体系设计和回测验证。

**优先级行动**（按天数估算）：
- **0-1 天**：类型提示 + 代码清理 + ruff 格式化（影响最大）。
- **1-3 天**：添加更多测试 + 性能日志。
- **3-7 天**：前端交互 + CI 增强。

实施后版本可升级至 v3.25，文档同步更新。

---

*本报告基于代码静态分析、运行测试（若有）和文档审查生成。如需进一步细节或具体文件修改，请提供更多上下文。*
