#!/usr/bin/env python3
"""
回测可视化工具 — 生成回测报告和图表

用法:
  python scripts/backtest_viz.py                    # 生成报告
  python scripts/backtest_viz.py --output report.html  # 指定输出文件
"""
import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")


def load_backtest_results():
    path = os.path.join(DATA_DIR, "backtest_results.json")
    if not os.path.exists(path):
        print("ERROR: backtest_results.json not found. Run backtest.py first.")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_history():
    path = os.path.join(os.path.dirname(__file__), "..", "web", "data", "history.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def calculate_metrics(results):
    valid = [r for r in results if "error" not in r and r.get("composite") is not None]
    if not valid:
        return {}

    scores = [r["composite"] for r in valid]
    [r.get("valuation", 0) or 0 for r in valid]

    bull_peaks = [r for r in valid if r["state"] in ("BULL_PEAK", "SURGE_PEAK")]
    bear_bottoms = [r for r in valid if r["state"] in ("BEAR_BOTTOM", "CRASH_BOTTOM")]

    return {
        "total_dates": len(valid),
        "avg_score": sum(scores) / len(scores),
        "max_score": max(scores),
        "min_score": min(scores),
        "bull_peak_avg": sum(r["composite"] for r in bull_peaks) / len(bull_peaks) if bull_peaks else 0,
        "bear_bottom_avg": sum(r["composite"] for r in bear_bottoms) / len(bear_bottoms) if bear_bottoms else 0,
        "accuracy": sum(1 for r in valid if (r["state"] in ("BULL_PEAK", "SURGE_PEAK") and r["composite"] >= 55) or
                       (r["state"] in ("BEAR_BOTTOM", "CRASH_BOTTOM") and r["composite"] <= 40)) / len(valid) * 100,
    }


def generate_html_report(results, history, metrics, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    dates = [r["date"] for r in results if "error" not in r]
    scores = [r.get("composite", 0) or 0 for r in results if "error" not in r]
    states = [r["state"] for r in results if "error" not in r]
    descs = [r.get("desc", "") for r in results if "error" not in r]
    dims = ['valuation', 'fund', 'sentiment', 'technical', 'structure']

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>回测报告 - A股牛市热度指数</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
body {{ font-family: -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ color: #58a6ff; border-bottom: 1px solid #21262d; padding-bottom: 10px; }}
.metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 20px 0; }}
.metric {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 16px; text-align: center; }}
.metric .value {{ font-size: 28px; font-weight: 700; color: #58a6ff; }}
.metric .label {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}
.chart {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 20px; margin: 20px 0; }}
.chart-title {{ font-size: 14px; font-weight: 600; color: #8b949e; margin-bottom: 16px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ background: #0d1117; color: #8b949e; padding: 10px; text-align: left; border-bottom: 1px solid #21262d; }}
td {{ padding: 10px; border-bottom: 1px solid #21262d; }}
tr:hover {{ background: #161b22; }}
.red {{ color: #f85149; }} .yellow {{ color: #d29922; }} .green {{ color: #3fb950; }}
</style>
</head>
<body>
<div class="container">
<h1>回测报告 - A股牛市热度指数</h1>
<p style="color: #8b949e;">生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

<div class="metrics">
  <div class="metric"><div class="value">{metrics.get('total_dates', 0)}</div><div class="label">测试日期数</div></div>
  <div class="metric"><div class="value">{metrics.get('avg_score', 0):.1f}</div><div class="label">平均热度</div></div>
  <div class="metric"><div class="value">{metrics.get('accuracy', 0):.0f}%</div><div class="label">信号准确率</div></div>
  <div class="metric"><div class="value">{metrics.get('bull_peak_avg', 0):.0f} / {metrics.get('bear_bottom_avg', 0):.0f}</div><div class="label">牛市顶/熊市底</div></div>
</div>

<div class="chart">
  <div class="chart-title">热度指数历史走势</div>
  <div id="scoreChart" style="height: 400px;"></div>
</div>

<div class="chart">
  <div class="chart-title">各维度得分对比</div>
  <div id="dimChart" style="height: 400px;"></div>
</div>

<h2>详细数据</h2>
<table>
<tr><th>日期</th><th>上证综指</th><th>状态</th><th>综合热度</th><th>估值</th><th>资金</th><th>情绪</th><th>技术</th><th>结构</th><th>描述</th></tr>
"""

    for r in results:
        if "error" in r:
            continue
        score = r.get("composite", 0) or 0
        level_class = "red" if score >= 65 else "yellow" if score >= 40 else "green"
        html += f"""<tr>
<td>{r['date']}</td>
<td>{r.get('sh_close', '')}</td>
<td>{r.get('state', '')}</td>
<td class="{level_class}">{score:.1f}</td>
<td>{r.get('valuation', 0) or 0:.1f}</td>
<td>{r.get('fund', 0) or 0:.1f}</td>
<td>{r.get('sentiment', 0) or 0:.1f}</td>
<td>{r.get('technical', 0) or 0:.1f}</td>
<td>{r.get('structure', 0) or 0:.1f}</td>
<td>{r.get('desc', '')}</td>
</tr>"""

    html += f"""</table>
</div>

<script>
const dims = {json.dumps(['valuation', 'fund', 'sentiment', 'technical', 'structure'])};
const dates = {json.dumps(dates)};
const scores = {json.dumps(scores)};
const states = {json.dumps(states)};
const descs = {json.dumps(descs)};

const scoreChart = echarts.init(document.getElementById('scoreChart'));
scoreChart.setOption({{
  tooltip: {{ trigger: 'axis', formatter: p => p[0].name + '<br/>热度: <b>' + p[0].value.toFixed(1) + '</b><br/>' + descs[p[0].dataIndex] }},
  grid: {{ top: 20, bottom: 30, left: 50, right: 20 }},
  xAxis: {{ type: 'category', data: dates, axisLabel: {{ color: '#8b949e', rotate: 45 }} }},
  yAxis: {{ type: 'value', min: 0, max: 100, axisLabel: {{ color: '#8b949e' }}, splitLine: {{ lineStyle: {{ color: '#21262d' }} }} }},
  series: [{{
    type: 'line', data: scores, smooth: true, symbol: 'circle', symbolSize: 8,
    lineStyle: {{ color: '#58a6ff', width: 2 }},
    itemStyle: {{ color: p => states[p.dataIndex]?.includes('PEAK') ? '#f85149' : states[p.dataIndex]?.includes('BOTTOM') ? '#3fb950' : '#58a6ff' }},
    markLine: {{ silent: true, data: [
      {{ yAxis: 65, lineStyle: {{ color: '#f85149', type: 'dashed' }}, label: {{ color: '#f85149', formatter: '红区 65' }} }},
      {{ yAxis: 40, lineStyle: {{ color: '#d29922', type: 'dashed' }}, label: {{ color: '#d29922', formatter: '黄区 40' }} }}
    ] }}
  }}]
}});

const dimChart = echarts.init(document.getElementById('dimChart'));
const dims = ['valuation', 'fund', 'sentiment', 'technical', 'structure'];
const dimNames = ['估值', '资金', '情绪', '技术', '结构'];
dimChart.setOption({{
  tooltip: {{ trigger: 'axis' }},
  legend: {{ data: dimNames, textStyle: {{ color: '#8b949e' }} }},
  grid: {{ top: 40, bottom: 30, left: 50, right: 20 }},
  xAxis: {{ type: 'category', data: dates, axisLabel: {{ color: '#8b949e', rotate: 45 }} }},
  yAxis: {{ type: 'value', min: 0, max: 100, axisLabel: {{ color: '#8b949e' }}, splitLine: {{ lineStyle: {{ color: '#21262d' }} }} }},
  series: dims.map((d, i) => ({{
    name: dimNames[i], type: 'line', smooth: true, symbol: 'none',
    data: {json.dumps({d: [r.get(d, 0) or 0 for r in results if "error" not in r] for d in dims})}[d]
  }}))
}});
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report generated: {output_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate backtest visualization report")
    parser.add_argument("--output", help="Output HTML file path")
    args = parser.parse_args()

    results = load_backtest_results()
    history = load_history()
    metrics = calculate_metrics(results)

    output = args.output or os.path.join(REPORT_DIR, f"backtest_report_{datetime.now().strftime('%Y%m%d')}.html")
    generate_html_report(results, history, metrics, output)


if __name__ == "__main__":
    main()
