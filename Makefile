.PHONY: daily backup restore serve test lint clean help

help:  ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

daily:  ## 运行今日计算
	python scripts/run_daily.py

daily-date:  ## 运行指定日期 (make daily-date DATE=2026-06-15)
	python scripts/run_daily.py $(DATE)

backup:  ## 备份数据库
	python scripts/db_compress.py backup

restore:  ## 从最新备份恢复
	python scripts/db_compress.py restore

backups:  ## 列出所有备份
	python scripts/db_compress.py list

size:  ## 显示数据库大小
	python scripts/db_compress.py size

serve:  ## 启动本地API + 前端 (localhost:8000)
	python scripts/api_server.py

web:  ## 启动前端仪表盘 (localhost:8080, 仅静态页面)
	@echo "Opening http://localhost:8080/app.html"
	@cd web && python3 -m http.server 8080

test:  ## 运行测试
	python -m pytest tests/ -v

lint:  ## 代码检查
	ruff check src/ scripts/ --select E,F,W --ignore E501
	ruff format --check src/ scripts/

format:  ## 代码格式化
	ruff format src/ scripts/

export:  ## 导出CSV (make export DAYS=30)
	python scripts/export_csv.py --days $(or $(DAYS),30)

compare:  ## 历史对比 (make compare DATE=2015-06-12)
	python scripts/compare_history.py $(DATE)

status:  ## 查看数据状态
	python scripts/data_manager.py status

backfill:  ## 补充数据
	python scripts/data_manager.py backfill

analyze:  ## 热度分析 (归因+异常+预测)
	python scripts/heat_analysis.py --all

report:  ## 生成回测报告
	python scripts/backtest_viz.py

clean:  ## 清理临时文件
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -f *.log
