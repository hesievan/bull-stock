#!/usr/bin/env python3
"""
热度指数 REST API — 基于 FastAPI

用法:
  python scripts/api_server.py                  # 默认 localhost:8000
  python scripts/api_server.py --port 9000      # 指定端口
  python scripts/api_server.py --host 0.0.0.0   # 允许外部访问

API:
  GET /api/heat          — 最新热度指数
  GET /api/history       — 历史数据 (?days=30)
  GET /api/sectors       — 板块热度
  GET /api/detail        — 详细指标拆解
  GET /api/strategy      — 策略信号
  GET /api/health        — 健康检查
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

WEB_DATA = Path(__file__).parent.parent / "web" / "data"


def _read_json(filename):
    p = WEB_DATA / filename
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def calculate_strategy_signal(heat_data):
    """计算策略信号"""
    if heat_data is None:
        return {"error": "No data"}

    score = heat_data.get("composite_score")
    dims = heat_data.get("dimensions", {})

    if score is None:
        return {"signal": "unknown", "reason": "数据缺失"}

    # 获取各维度分数
    valuation = dims.get("valuation", {}).get("score", 50)
    sentiment = dims.get("sentiment", {}).get("score", 50)
    fund = dims.get("fund", {}).get("score", 50)

    # 热度等级
    if score >= 65:
        level = "red"
        level_cn = "红色预警"
    elif score >= 55:
        level = "orange"
        level_cn = "橙色关注"
    elif score >= 40:
        level = "yellow"
        level_cn = "黄色警惕"
    else:
        level = "green"
        level_cn = "绿色安全"

    # 信号判断
    signal = "hold"
    reason = ""
    risk_level = "low"

    # 最强减仓信号
    if score >= 70 and valuation >= 90:
        signal = "clear"
        reason = "综合热度≥70且估值极高，建议清仓"
        risk_level = "extreme"
    elif score >= 65:
        signal = "reduce"
        reason = "综合热度进入红色区间，建议大幅减仓"
        risk_level = "high"
    elif score >= 55:
        signal = "reduce"
        reason = "综合热度进入橙色区间，建议分批减仓"
        risk_level = "medium"
    elif valuation >= 80 and sentiment >= 80:
        signal = "reduce"
        reason = "估值和情绪同时过热，建议减仓"
        risk_level = "high"
    elif score <= 35 and valuation <= 40:
        signal = "add"
        reason = "热度低且估值低，可考虑加仓"
        risk_level = "low"
    else:
        signal = "hold"
        reason = "热度中性，维持当前仓位"
        risk_level = "low" if score < 50 else "medium"

    # 目标仓位计算
    if valuation < 30:
        base_position = 95
    elif valuation < 60:
        base_position = 75
    elif valuation < 80:
        base_position = 55
    else:
        base_position = 25

    if score < 30:
        factor = 1.0
    elif score < 50:
        factor = 0.9
    elif score < 60:
        factor = 0.8
    elif score < 70:
        factor = 0.6
    else:
        factor = 0.3

    target_position = max(20, min(100, base_position * factor))

    return {
        "signal": signal,
        "signal_cn": {"hold": "持有", "reduce": "减仓", "add": "加仓", "clear": "清仓"}.get(signal, "未知"),
        "level": level,
        "level_cn": level_cn,
        "target_position": round(target_position, 1),
        "reason": reason,
        "risk_level": risk_level,
        "risk_cn": {"low": "低风险", "medium": "中风险", "high": "高风险", "extreme": "极高风险"}.get(risk_level, "未知"),
        "indicators": {
            "heat_score": score,
            "valuation": valuation,
            "sentiment": sentiment,
            "fund": fund,
        }
    }


def create_app():
    try:
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.staticfiles import StaticFiles
    except ImportError:
        print("ERROR: fastapi not installed. Run: pip install fastapi uvicorn")
        sys.exit(1)

    app = FastAPI(title="A股牛市热度指数 API", version="4.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # 静态文件服务 — 前端仪表盘
    web_dir = Path(__file__).parent.parent / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    @app.get("/api/heat")
    def get_heat():
        data = _read_json("index.json")
        if data is None:
            return {"error": "No data available"}
        return data

    @app.get("/api/history")
    def get_history(days: int = 30):
        data = _read_json("history.json")
        if data is None:
            return {"error": "No history available"}
        return data[-days:]

    @app.get("/api/sectors")
    def get_sectors():
        data = _read_json("sectors.json")
        if data is None:
            return {"error": "No sector data available"}
        return data

    @app.get("/api/detail")
    def get_detail():
        data = _read_json("detail.json")
        if data is None:
            return {"error": "No detail data available"}
        return data

    @app.get("/api/strategy")
    def get_strategy():
        heat_data = _read_json("index.json")
        if heat_data is None:
            return {"error": "No data available"}
        return calculate_strategy_signal(heat_data)

    @app.get("/api/health")
    def health():
        index = _read_json("index.json")
        return {
            "status": "ok",
            "data_date": index.get("trade_date") if index else None,
            "generated_at": index.get("updated_at") if index else None,
        }

    return app


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Heat Index API Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    app = create_app()
    try:
        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port)
    except ImportError:
        print("ERROR: uvicorn not installed. Run: pip install uvicorn")
        sys.exit(1)
