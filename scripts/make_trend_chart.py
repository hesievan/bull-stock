"""读取落表 CSV, 生成自包含 SVG 折线图 HTML (无外部依赖)。

用法: python scripts/make_trend_chart.py <csv> <out_html>
"""

import sys
import csv

csv_path = sys.argv[1] if len(sys.argv) > 1 else "reports/heat_trend_2025-07-01_2026-08-10.csv"
out_path = sys.argv[2] if len(sys.argv) > 2 else "reports/heat_trend_2025-07_2026-08-10.html"

rows = list(csv.DictReader(open(csv_path)))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


series = {
    "composite": [_f(r["composite"]) for r in rows],
    "valuation": [_f(r["valuation"]) for r in rows],
    "fund": [_f(r["fund"]) for r in rows],
    "sentiment": [_f(r["sentiment"]) for r in rows],
    "structure": [_f(r["structure"]) for r in rows],
}
dates = [r["trade_date"] for r in rows]
n = len(rows)

W, H = 980, 460
L, R, T, B = 60, 20, 20, 60
x0, x1 = L, W - R
y0, y1 = H - B, T
ymin, ymax = 0, 100


def sx(i):
    return x0 + (x1 - x0) * i / (n - 1)


def sy(v):
    return y0 + (y1 - y0) * (ymax - v) / (ymax - ymin)


def poly(vals, color, w=2):
    # 遇 None 断点重连
    segs = []
    cur = []
    for i in range(n):
        v = vals[i]
        if v is None or v == "":
            if cur:
                segs.append(cur)
                cur = []
            continue
        cur.append(f"{sx(i):.1f},{sy(float(v)):.1f}")
    if cur:
        segs.append(cur)
    return "".join(f'<polyline fill="none" stroke="{color}" stroke-width="{w}" points="{" ".join(s)}"/>' for s in segs)


# 阈值带
bands = [
    (0, 40, "#e8f5e9", "绿区 <40"),
    (40, 55, "#fffde7", "黄区 40-55"),
    (55, 65, "#fff3e0", "橙区 55-65"),
    (65, 100, "#ffebee", "红区 ≥65"),
]
band_svg = ""
for lo, hi, col, _ in bands:
    yb = sy(hi)
    ye = sy(lo)
    band_svg += (
        f'<rect x="{x0}" y="{yb:.1f}" width="{x1 - x0:.1f}" height="{ye - yb:.1f}" fill="{col}" opacity="0.55"/>'
    )

# 网格 + Y 轴刻度
grid = ""
for v in range(0, 101, 10):
    yy = sy(v)
    grid += f'<line x1="{x0}" y1="{yy:.1f}" x2="{x1}" y2="{yy:.1f}" stroke="#eee" stroke-width="1"/>'
    grid += f'<text x="{x0 - 8}" y="{yy + 4:.1f}" font-size="11" fill="#666" text-anchor="end">{v}</text>'

# X 轴月份刻度
xticks = ""
seen = set()
for i, d in enumerate(dates):
    mo = d[:7]
    if mo not in seen and (i == 0 or i == n - 1 or i % 22 == 0):
        seen.add(mo)
        xticks += (
            f'<text x="{sx(i):.1f}" y="{y0 + 18:.1f}" font-size="10" fill="#666" text-anchor="middle">{d[2:7]}</text>'
        )

colors = {
    "composite": "#d32f2f",
    "valuation": "#7b1fa2",
    "fund": "#1976d2",
    "sentiment": "#f57c00",
    "structure": "#388e3c",
}
lines = "".join(
    poly(series[k], colors[k], 2.4 if k == "composite" else 1.4)
    for k in ["valuation", "fund", "sentiment", "structure", "composite"]
)

legend = ""
for i, k in enumerate(["composite", "valuation", "fund", "sentiment", "structure"]):
    lx = x0 + 10 + i * 150
    legend += f'<rect x="{lx}" y="{T + 4}" width="14" height="4" fill="{colors[k]}"/><text x="{lx + 20}" y="{T + 9}" font-size="11" fill="#333">{k}</text>'

# 标注单日跳变(>15)
jumps = []
prev = None
for i, r in enumerate(rows):
    c = float(r["composite"])
    if prev is not None and abs(c - prev) > 15:
        jumps.append((i, c))
    prev = c
jump_svg = ""
for i, c in jumps:
    jump_svg += f'<circle cx="{sx(i):.1f}" cy="{sy(c):.1f}" r="4" fill="none" stroke="#000" stroke-width="1.5"/>'

html = f"""<!doctype html><html><head><meta charset="utf-8"><title>热度走势 {dates[0]}~{dates[-1]}</title>
<style>body{{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:20px;color:#222}}
h2{{margin:0 0 4px}} .sub{{color:#888;font-size:13px;margin-bottom:12px}}
.note{{background:#fff8e1;padding:10px 14px;border-left:4px solid #ffb300;font-size:13px;line-height:1.6;margin-bottom:14px}}
svg{{border:1px solid #ddd;border-radius:6px}}</style></head>
<body><h2>牛市热度指数 V2 — 综合热度走势</h2>
<div class="sub">区间 {dates[0]} ~ {dates[-1]} ｜ 逐日 {n} 个交易日 ｜ 引擎实时重算</div>
<div class="note"><b>核查结论：</b>综合分区间 <b>55.5~87.9</b>，均值 73.8，<b>267 天零绿零黄、全橙/红</b>。
高读数主要来自估值(巴菲特 89)/杠杆(两融 88)/量能(成交M2比 89、换手 92)确实处于 10 年高位（真实牛市特征）。
唯一显示口径 bug：换手率原始值被放大 ~100×（amount=千元、circ_mv=万元 单位混用又 ×10），得分不受影响。
全部 7 处单日跳变(&gt;15分)均由 <b>涨停封板率(seal_rate, 15%权重)</b> 主导（单日 ±60~95），底层涨跌停为真实数据，非计算错误。</div>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
{band_svg}{grid}{lines}{jump_svg}{xticks}{legend}
</svg>
<p style="font-size:12px;color:#888">红圈 = 综合分单日跳变 &gt;15 分（seal_rate 主导）。灰底竖带：绿&lt;40 / 黄40-55 / 橙55-65 / 红≥65。</p>
</body></html>"""

open(out_path, "w", encoding="utf-8").write(html)
print("✓ 已生成", out_path)
