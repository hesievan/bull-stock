#!/usr/bin/env python3
"""
持仓监控日报 - 盘中实时版
数据源: 腾讯财经实时行情(盘中) + 本地 stock_daily(DB历史均线/量比)
推送时间: 每交易日 11:00, 14:00, 16:00
"""

import sys
import sqlite3
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "heat_index.db"

# (名称, 腾讯代码, tushare格式代码, DB代码)
HOLDINGS = [
    ("顺丰控股", "sz002352", "002352.SZ", "sz002352"),
    ("五粮液",   "sz000858", "000858.SZ", "sz000858"),
    ("中国平安", "sh601318", "601318.SH", "sh601318"),
    ("公牛集团", "sh603195", "603195.SH", "sh603195"),
    ("吉祥航空", "sh603885", "603885.SH", "sh603885"),
    ("上海机场", "sh600009", "600009.SH", "sh600009"),
    ("平安银行", "sz000001", "000001.SZ", "sz000001"),
    ("汤臣倍健", "sz300146", "300146.SZ", "sz300146"),
]

FEISHU_WEBHOOK = "https://www.feishu.cn/flow/api/trigger-webhook/18d944beda7772e52c8e326e34b40da0"


def fetch_realtime_tencent():
    """腾讯财经实时行情"""
    tencent_codes = [h[1] for h in HOLDINGS]
    url = f"http://qt.gtimg.cn/q={','.join(tencent_codes)}"
    r = requests.get(url, timeout=10)
    result = {}
    for line in r.text.strip().split("\n"):
        if not line.strip() or "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) < 45:
            continue
        name = parts[1].strip()
        code = parts[2].strip()
        try:
            close = float(parts[3])
            chg_pct = float(parts[32])
            vol = float(parts[6])  # 万手
            amount = float(parts[37])  # 万元
            today_open = float(parts[5]) if parts[5] else 0
            prev_close = float(parts[4]) if parts[4] else 0
            high = float(parts[33]) if parts[33] else 0
            low = float(parts[34]) if parts[34] else 0
        except (ValueError, IndexError):
            continue
        result[code] = {
            "name": name, "close": close, "chg": chg_pct,
            "vol": vol, "amount": amount,
            "open": today_open, "prev_close": prev_close,
            "high": high, "low": low,
        }
    return result


def fetch_history(db_code, days=120):
    """本地 stock_daily 读历史数据"""
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        "SELECT trade_date, close, volume, amount FROM stock_daily "
        "WHERE stock_code = ? ORDER BY trade_date DESC LIMIT ?",
        conn, params=(db_code, days * 2)
    )
    conn.close()
    if df.empty:
        return pd.DataFrame()
    for c in ["close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("trade_date").tail(days).reset_index(drop=True)


def build_report():
    now = datetime.now()
    lines = [f"📊 **持仓监控日报** · {now.strftime('%Y-%m-%d %H:%M')}\n"]

    # 1. 腾讯实时数据
    try:
        rt = fetch_realtime_tencent()
    except Exception as e:
        return f"❌ 腾讯实时接口调用失败: {e}"

    if not rt:
        return "❌ 未获取到实时数据"

    results = []
    for name, tencent_code, ts_code, db_code in HOLDINGS:
        try:
            code_num = tencent_code[2:]  # 去掉 sh/sz 前缀
            if code_num not in rt:
                lines.append(f"**{name}** — ⚠️ 无数据\n")
                continue

            q = rt[code_num]
            close = q["close"]
            chg = q["chg"]
            vol = q["vol"]  # 万手
            amount = q["amount"]  # 万元

            # 历史数据 (需要250天以支持MA200)
            hist = fetch_history(db_code, days=250)
            close_arr = hist["close"].values.astype(float) if not hist.empty else []
            amt_arr = hist["amount"].values.astype(float) if not hist.empty else []
            n = len(close_arr)

            # 均线
            ma = {}
            for p in [5, 20, 60, 200]:
                ma[p] = round(float(close_arr[-p:].mean()), 2) if n >= p else None

            # 趋势
            if ma[5] and ma[20] and ma[60]:
                above = sum(1 for p in [5, 20, 60, 200] if close > ma[p])
                if above == 4:
                    trend, te = "上升趋势", "🟢"
                elif above == 0:
                    trend, te = "下降趋势", "🔴"
                else:
                    trend, te = "震荡", "🟡"
            else:
                trend, te = "数据不足", "⚪"

            # MA20 偏离
            bias20 = ((close - ma[20]) / ma[20] * 100) if ma[20] else None

            # 量比: 今日成交额 / 5日均成交额
            # 本地 amount 单位千元, 腾讯 amount 单位万元
            vol_ratio = None
            avg5_amt = None
            avg20_amt = None
            if n >= 5:
                hist_avg5 = float(amt_arr[-5:].mean()) / 1e2  # 千元→万元
                if hist_avg5 > 0:
                    vol_ratio = amount / hist_avg5
                avg5_amt = hist_avg5 / 1e4  # 万元→亿元
            if n >= 20:
                avg20_amt = float(amt_arr[-20:].mean()) / 1e2 / 1e4  # 千元→亿元

            # 区间涨跌
            chg5 = (close / close_arr[-6] - 1) * 100 if n >= 6 else None
            chg20 = (close / close_arr[-21] - 1) * 100 if n >= 21 else None

            results.append((name, close, chg))

            # 输出
            ce = "📈" if chg > 0 else "📉" if chg < 0 else "➡️"
            lines.append(f"**{name}** ({ts_code})  {close:.2f}  {ce}{chg:+.2f}%  {te} {trend}")

            # 均线
            ma_parts = []
            for p in [5, 20, 60, 200]:
                v = ma.get(p)
                if v:
                    pos = "↑" if close > v else "↓"
                    ma_parts.append(f"MA{p}={v:.2f}{pos}")
            if ma_parts:
                lines.append(f"  均线: {' · '.join(ma_parts)}")

            if bias20 is not None:
                lines.append(f"  MA20偏离: {bias20:+.1f}%")

            # 成交
            parts = [f"成交 {amount/1e4:.2f}亿", f"量 {vol:.0f}万手"]
            if vol_ratio is not None:
                parts.append(f"量比 {vol_ratio:.2f}")
            lines.append(f"  {' · '.join(parts)}")

            if avg5_amt and avg20_amt:
                lines.append(f"  5日均量 {avg5_amt:.2f}亿 · 20日均量 {avg20_amt:.2f}亿")

            # 区间
            period = []
            if chg5 is not None:
                period.append(f"5日{chg5:+.1f}%")
            if chg20 is not None:
                period.append(f"20日{chg20:+.1f}%")
            if period:
                lines.append(f"  {' · '.join(period)}")

            lines.append("")
        except Exception as e:
            lines.append(f"**{name}** ({ts_code}) — ⚠️ 异常: {e}\n")

    # 汇总
    if results:
        lines.append("---")
        up = sum(1 for _, _, c in results if c > 0)
        down = sum(1 for _, _, c in results if c < 0)
        flat = len(results) - up - down
        lines.append(f"📈 上涨 {up} · 📉 下跌 {down} · ➡️ 平盘 {flat}")
        sorted_r = sorted(results, key=lambda x: x[2], reverse=True)
        if sorted_r:
            lines.append(f"🏆 最强: {sorted_r[0][0]} {sorted_r[0][2]:+.2f}%")
            lines.append(f"💀 最弱: {sorted_r[-1][0]} {sorted_r[-1][2]:+.2f}%")

    lines.append("\n---\n> ⚠️ 不构成投资建议，仅供参考")
    return "\n".join(lines)


def push_feishu(text):
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        print(f"Feishu push: {r.status_code}")
    except Exception as e:
        print(f"Feishu push failed: {e}")


if __name__ == "__main__":
    report = build_report()
    print(report)

    out_dir = PROJECT_ROOT / "reports" / "watchlist"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    out_file.write_text(report, encoding="utf-8")
    print(f"\n--- saved to {out_file} ---")

    push_feishu(report)
