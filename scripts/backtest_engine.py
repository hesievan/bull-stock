#!/usr/bin/env python3
"""
完整回测引擎 — 基于热度指数信号模拟交易

用法:
  python scripts/backtest_engine.py                    # 默认参数回测
  python scripts/backtest_engine.py --start 2018-01-01 # 指定起始日期
  python scripts/backtest_engine.py --initial 1000000  # 初始资金100万
  python scripts/backtest_engine.py --report           # 生成HTML报告
"""
import sys
import os
import json
import sqlite3
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import DB_PATH
from src.indicators.calculator import calculate_heat_index

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")


class BacktestEngine:
    """基于热度指数的回测引擎"""

    def __init__(self, initial_capital: float = 1000000, commission: float = 0.001):
        self.initial_capital = initial_capital
        self.commission = commission  # 单边手续费
        self.trades: List[Dict] = []
        self.equity_curve: List[Dict] = []

    def get_index_prices(self, start: str, end: str) -> List[Dict]:
        """获取指数价格数据"""
        conn = sqlite3.connect(DB_PATH)
        try:
            rows = conn.execute("""
                SELECT trade_date, close, pct_change
                FROM index_daily
                WHERE index_code='sh000001'
                  AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
            """, (start, end)).fetchall()
            return [{"date": r[0], "close": r[1], "pct_change": r[2] or 0} for r in rows]
        finally:
            conn.close()

    def get_heat_history(self, start: str, end: str) -> Dict[str, Dict]:
        """获取热度指数历史 (支持周频数据插值到日频)"""
        history_file = os.path.join(os.path.dirname(__file__), "..", "web", "data", "history.json")
        if not os.path.exists(history_file):
            return {}
        with open(history_file, encoding="utf-8") as f:
            history = json.load(f)

        # 构建日频数据 (周频数据向前填充)
        result = {}
        last_score = None
        for h in history:
            if h["trade_date"] < start:
                last_score = h.get("composite_score")
                continue
            if h["trade_date"] > end:
                break
            result[h["trade_date"]] = h
            last_score = h.get("composite_score")

        return result

    def calculate_signal(self, score: float, prev_score: float = None, position: float = 0) -> str:
        """
        根据热度分数计算交易信号
        signal: 'buy' | 'sell' | 'hold'
        """
        if score is None:
            return "hold"

        # 红色预警 (>=65): 卖出信号
        if score >= 65:
            return "sell"

        # 橙色关注 (55-60): 考虑减仓 (仅在高仓位时)
        if score >= 55 and position > 0.5:
            return "sell"

        # 绿色安全 (<=35): 买入信号
        if score <= 35 and position < 0.3:
            return "buy"

        # 深度绿色 (<=25): 强烈买入
        if score <= 25 and position < 0.8:
            return "buy"

        return "hold"

    def calculate_position(self, signal: str, current_position: float, score: float) -> float:
        """根据信号计算目标仓位 (0-1)"""
        if signal == "buy":
            # 买入：根据热度调整买入量
            if score <= 20:
                return min(1.0, current_position + 0.3)
            elif score <= 30:
                return min(1.0, current_position + 0.2)
            else:
                return min(1.0, current_position + 0.1)
        elif signal == "sell":
            # 卖出：根据热度调整卖出量
            if score >= 70:
                return max(0.0, current_position - 0.5)
            elif score >= 60:
                return max(0.0, current_position - 0.3)
            else:
                return max(0.0, current_position - 0.2)
        return current_position

    def run(self, start: str = "2015-01-01", end: str = None) -> Dict:
        """执行回测"""
        if end is None:
            end = date.today().strftime("%Y-%m-%d")

        print(f"回测区间: {start} ~ {end}")
        print(f"初始资金: {self.initial_capital:,.0f}")
        print(f"手续费: {self.commission*100:.1f}%")
        print("-" * 60)

        # 获取数据
        prices = self.get_index_prices(start, end)
        heat_history = self.get_heat_history(start, end)

        if not prices:
            print("ERROR: 无价格数据")
            return {}

        # 初始化
        cash = self.initial_capital
        shares = 0
        position = 0.0  # 当前仓位比例
        prev_score = None
        trades = []
        equity_curve = []

        # 逐日模拟
        for i, price in enumerate(prices):
            trade_date = price["date"]
            close = price["close"]

            # 获取热度分数
            heat = heat_history.get(trade_date)
            score = heat["composite_score"] if heat else None

            # 计算信号
            signal = self.calculate_signal(score, prev_score, position)

            # 执行交易
            if signal == "buy" and position < 0.9:
                new_position = self.calculate_position(signal, position, score)
                # 计算需要买入的金额 (基于当前总资产)
                current_value = cash + shares * close
                target_value = current_value * new_position
                buy_value = target_value - (shares * close)
                if buy_value > 0:
                    # 买入
                    buy_amount = buy_value / (1 + self.commission)
                    if buy_amount > 0 and cash >= buy_amount:
                        buy_shares = buy_amount / close
                        cost = buy_amount * (1 + self.commission)
                        cash -= cost
                        shares += buy_shares
                        position = new_position
                        trades.append({
                            "date": trade_date, "action": "BUY",
                            "price": close, "shares": buy_shares,
                            "amount": buy_amount, "score": score
                        })

            elif signal == "sell" and position > 0.1:
                new_position = self.calculate_position(signal, position, score)
                # 计算需要卖出的金额 (基于当前总资产)
                current_value = cash + shares * close
                target_value = current_value * new_position
                sell_value = (shares * close) - target_value
                if sell_value > 0:
                    # 卖出
                    sell_amount = sell_value / (1 - self.commission)
                    if sell_amount > 0 and shares > 0:
                        sell_shares = min(shares, sell_amount / close)
                        revenue = sell_shares * close * (1 - self.commission)
                        cash += revenue
                        shares -= sell_shares
                        position = new_position
                        trades.append({
                            "date": trade_date, "action": "SELL",
                            "price": close, "shares": sell_shares,
                            "amount": revenue, "score": score
                        })

            # 记录权益
            total_value = cash + shares * close
            equity_curve.append({
                "date": trade_date,
                "close": close,
                "score": score,
                "position": position,
                "cash": cash,
                "shares": shares,
                "total_value": total_value,
                "signal": signal,
            })

            prev_score = score

        # 计算绩效指标
        metrics = self.calculate_metrics(equity_curve, trades)

        # 输出结果
        self.print_results(metrics, trades, equity_curve)

        return {
            "metrics": metrics,
            "trades": trades,
            "equity_curve": equity_curve,
        }

    def calculate_metrics(self, equity_curve: List[Dict], trades: List[Dict]) -> Dict:
        """计算绩效指标"""
        if not equity_curve:
            return {}

        # 基础指标
        initial_value = equity_curve[0]["total_value"]
        final_value = equity_curve[-1]["total_value"]
        total_return = (final_value / initial_value - 1) * 100

        # 年化收益
        days = len(equity_curve)
        years = days / 252
        annual_return = ((final_value / initial_value) ** (1 / years) - 1) * 100 if years > 0 else 0

        # 最大回撤
        peak = initial_value
        max_drawdown = 0
        max_drawdown_date = ""
        for point in equity_curve:
            if point["total_value"] > peak:
                peak = point["total_value"]
            drawdown = (peak - point["total_value"]) / peak * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_date = point["date"]

        # 买入持有收益
        buy_hold_return = (equity_curve[-1]["close"] / equity_curve[0]["close"] - 1) * 100

        # 夏普比率 (简化计算)
        returns = []
        for i in range(1, len(equity_curve)):
            r = (equity_curve[i]["total_value"] / equity_curve[i-1]["total_value"] - 1)
            returns.append(r)
        if returns:
            avg_return = sum(returns) / len(returns)
            std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
            sharpe = (avg_return / std_return * (252 ** 0.5)) if std_return > 0 else 0
        else:
            sharpe = 0

        # 胜率
        buy_trades = [t for t in trades if t["action"] == "BUY"]
        sell_trades = [t for t in trades if t["action"] == "SELL"]
        win_count = 0
        total_pairs = min(len(buy_trades), len(sell_trades))
        for i in range(total_pairs):
            if sell_trades[i]["amount"] > buy_trades[i]["amount"]:
                win_count += 1
        win_rate = (win_count / total_pairs * 100) if total_pairs > 0 else 0

        return {
            "initial_capital": initial_value,
            "final_value": final_value,
            "total_return": total_return,
            "annual_return": annual_return,
            "max_drawdown": max_drawdown,
            "max_drawdown_date": max_drawdown_date,
            "buy_hold_return": buy_hold_return,
            "sharpe_ratio": sharpe,
            "total_trades": len(trades),
            "win_rate": win_rate,
            "years": years,
        }

    def print_results(self, metrics: Dict, trades: List[Dict], equity_curve: List[Dict]):
        """输出回测结果"""
        print("\n" + "=" * 60)
        print("回测结果")
        print("=" * 60)

        print(f"\n初始资金:     {metrics['initial_capital']:>15,.0f}")
        print(f"最终价值:     {metrics['final_value']:>15,.0f}")
        print(f"总收益:       {metrics['total_return']:>14.2f}%")
        print(f"年化收益:     {metrics['annual_return']:>14.2f}%")
        print(f"最大回撤:     {metrics['max_drawdown']:>14.2f}%")
        print(f"回撤日期:     {metrics['max_drawdown_date']:>15}")
        print(f"买入持有:     {metrics['buy_hold_return']:>14.2f}%")
        print(f"夏普比率:     {metrics['sharpe_ratio']:>15.2f}")
        print(f"总交易次数:   {metrics['total_trades']:>15}")
        print(f"胜率:         {metrics['win_rate']:>14.1f}%")
        print(f"回测年数:     {metrics['years']:>14.1f}")

        # 策略 vs 买入持有
        alpha = metrics['total_return'] - metrics['buy_hold_return']
        print(f"\n超额收益(Alpha): {alpha:+.2f}%")

        # 最近10笔交易
        if trades:
            print(f"\n最近10笔交易:")
            print("-" * 60)
            for t in trades[-10:]:
                score_str = f"{t['score']:.0f}" if t['score'] else "N/A"
                print(f"  {t['date']} {t['action']:4} @ {t['price']:.2f}  "
                      f"金额: {t['amount']:>10,.0f}  热度: {score_str}")


def generate_report(result: Dict, output_path: str = None):
    """生成HTML回测报告"""
    metrics = result["metrics"]
    equity = result["equity_curve"]
    trades = result["trades"]

    if output_path is None:
        output_path = os.path.join(REPORT_DIR, f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 准备图表数据
    dates = [e["date"] for e in equity]
    scores = [e["score"] or 0 for e in equity]
    strategy_values = [e["total_value"] for e in equity]
    buy_hold_values = [equity[0]["total_value"] * e["close"] / equity[0]["close"] for e in equity]
    positions = [e["position"] * 100 for e in equity]

    # 买卖信号
    buy_signals = [{"date": t["date"], "price": t["price"]} for t in trades if t["action"] == "BUY"]
    sell_signals = [{"date": t["date"], "price": t["price"]} for t in trades if t["action"] == "SELL"]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>回测报告 - A股牛市热度指数</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
body {{ font-family: -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
h1 {{ color: #58a6ff; border-bottom: 1px solid #21262d; padding-bottom: 10px; }}
.metrics {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin: 20px 0; }}
.metric {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 16px; text-align: center; }}
.metric .value {{ font-size: 24px; font-weight: 700; color: #58a6ff; }}
.metric .label {{ font-size: 11px; color: #8b949e; margin-top: 4px; }}
.metric.positive .value {{ color: #3fb950; }}
.metric.negative .value {{ color: #f85149; }}
.chart {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 20px; margin: 20px 0; }}
.chart-title {{ font-size: 14px; font-weight: 600; color: #8b949e; margin-bottom: 16px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
th {{ background: #0d1117; color: #8b949e; padding: 10px; text-align: left; border-bottom: 1px solid #21262d; }}
td {{ padding: 10px; border-bottom: 1px solid #21262d; }}
tr:hover {{ background: #161b22; }}
.buy {{ color: #3fb950; }} .sell {{ color: #f85149; }}
</style>
</head>
<body>
<div class="container">
<h1>回测报告 - A股牛市热度指数</h1>
<p style="color: #8b949e;">生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

<div class="metrics">
  <div class="metric {'positive' if metrics['total_return'] > 0 else 'negative'}"><div class="value">{metrics['total_return']:+.1f}%</div><div class="label">总收益</div></div>
  <div class="metric {'positive' if metrics['annual_return'] > 0 else 'negative'}"><div class="value">{metrics['annual_return']:+.1f}%</div><div class="label">年化收益</div></div>
  <div class="metric negative"><div class="value">{metrics['max_drawdown']:.1f}%</div><div class="label">最大回撤</div></div>
  <div class="metric"><div class="value">{metrics['sharpe_ratio']:.2f}</div><div class="label">夏普比率</div></div>
  <div class="metric"><div class="value">{metrics['win_rate']:.0f}%</div><div class="label">胜率</div></div>
</div>

<div class="metrics">
  <div class="metric"><div class="value">{metrics['initial_capital']:,.0f}</div><div class="label">初始资金</div></div>
  <div class="metric"><div class="value">{metrics['final_value']:,.0f}</div><div class="label">最终价值</div></div>
  <div class="metric {'positive' if metrics['total_return'] > metrics['buy_hold_return'] else 'negative'}"><div class="value">{metrics['total_return'] - metrics['buy_hold_return']:+.1f}%</div><div class="label">超额收益(Alpha)</div></div>
  <div class="metric"><div class="value">{metrics['buy_hold_return']:+.1f}%</div><div class="label">买入持有收益</div></div>
  <div class="metric"><div class="value">{metrics['total_trades']}</div><div class="label">交易次数</div></div>
</div>

<div class="chart">
  <div class="chart-title">权益曲线 (策略 vs 买入持有)</div>
  <div id="equityChart" style="height: 400px;"></div>
</div>

<div class="chart">
  <div class="chart-title">热度指数 & 仓位</div>
  <div id="scoreChart" style="height: 300px;"></div>
</div>

<h2>交易记录</h2>
<table>
<tr><th>日期</th><th>操作</th><th>价格</th><th>金额</th><th>热度</th></tr>
"""

    for t in trades:
        action_class = "buy" if t["action"] == "BUY" else "sell"
        score_str = f"{t['score']:.0f}" if t.get("score") else "N/A"
        html += f"""<tr>
<td>{t['date']}</td>
<td class="{action_class}">{t['action']}</td>
<td>{t['price']:.2f}</td>
<td>{t['amount']:,.0f}</td>
<td>{score_str}</td>
</tr>"""

    html += f"""</table>
</div>

<script>
const dates = {json.dumps(dates)};
const scores = {json.dumps(scores)};
const strategyValues = {json.dumps(strategy_values)};
const buyHoldValues = {json.dumps(buy_hold_values)};
const positions = {json.dumps(positions)};
const buySignals = {json.dumps(buy_signals)};
const sellSignals = {json.dumps(sell_signals)};

// 权益曲线
const equityChart = echarts.init(document.getElementById('equityChart'));
equityChart.setOption({{
  tooltip: {{ trigger: 'axis' }},
  legend: {{ data: ['策略', '买入持有'], textStyle: {{ color: '#8b949e' }} }},
  grid: {{ top: 40, bottom: 30, left: 60, right: 20 }},
  xAxis: {{ type: 'category', data: dates, axisLabel: {{ color: '#8b949e', rotate: 45 }} }},
  yAxis: {{ type: 'value', axisLabel: {{ color: '#8b949e', formatter: v => (v/10000).toFixed(0) + '万' }}, splitLine: {{ lineStyle: {{ color: '#21262d' }} }} }},
  series: [
    {{ name: '策略', type: 'line', data: strategyValues, smooth: true, symbol: 'none', lineStyle: {{ color: '#58a6ff', width: 2 }}, areaStyle: {{ color: 'rgba(88,166,255,0.1)' }} }},
    {{ name: '买入持有', type: 'line', data: buyHoldValues, smooth: true, symbol: 'none', lineStyle: {{ color: '#8b949e', width: 1.5, type: 'dashed' }} }},
    {{ type: 'scatter', name: '买入', data: buySignals.map(s => [s.date, strategyValues[dates.indexOf(s.date)] || 0]), symbol: 'triangle', symbolSize: 12, itemStyle: {{ color: '#3fb950' }} }},
    {{ type: 'scatter', name: '卖出', data: sellSignals.map(s => [s.date, strategyValues[dates.indexOf(s.date)] || 0]), symbol: 'pin', symbolSize: 12, itemStyle: {{ color: '#f85149' }} }}
  ]
}});

// 热度 & 仓位
const scoreChart = echarts.init(document.getElementById('scoreChart'));
scoreChart.setOption({{
  tooltip: {{ trigger: 'axis' }},
  legend: {{ data: ['热度', '仓位%'], textStyle: {{ color: '#8b949e' }} }},
  grid: {{ top: 40, bottom: 30, left: 50, right: 50 }},
  xAxis: {{ type: 'category', data: dates, axisLabel: {{ color: '#8b949e', rotate: 45 }} }},
  yAxis: [
    {{ type: 'value', min: 0, max: 100, axisLabel: {{ color: '#8b949e' }}, splitLine: {{ lineStyle: {{ color: '#21262d' }} }} }},
    {{ type: 'value', min: 0, max: 100, axisLabel: {{ color: '#8b949e', formatter: '{{value}}%' }}, splitLine: {{ show: false }} }}
  ],
  series: [
    {{ name: '热度', type: 'line', data: scores, smooth: true, symbol: 'none', lineStyle: {{ color: '#d29922', width: 2 }}, markLine: {{ silent: true, data: [{{ yAxis: 65, lineStyle: {{ color: '#f8514950', type: 'dashed' }} }}, {{ yAxis: 40, lineStyle: {{ color: '#d2992250', type: 'dashed' }} }}] }} }},
    {{ name: '仓位%', type: 'bar', data: positions, yAxisIndex: 1, itemStyle: {{ color: 'rgba(88,166,255,0.3)' }} }}
  ]
}});
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n报告已生成: {output_path}")
    return output_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backtest engine based on heat index")
    parser.add_argument("--start", default="2015-01-01", help="Start date")
    parser.add_argument("--end", help="End date")
    parser.add_argument("--initial", type=float, default=1000000, help="Initial capital")
    parser.add_argument("--commission", type=float, default=0.001, help="Commission rate")
    parser.add_argument("--report", action="store_true", help="Generate HTML report")
    args = parser.parse_args()

    engine = BacktestEngine(initial_capital=args.initial, commission=args.commission)
    result = engine.run(start=args.start, end=args.end)

    if args.report and result:
        generate_report(result)


if __name__ == "__main__":
    main()
