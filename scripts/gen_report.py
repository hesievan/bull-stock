#!/usr/bin/env python3
"""
牛市热度指数日报生成器 v3.5
输出: MD + HTML(带ECharts交互图) + PNG(精简信息图)
"""
import json
import os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'web', 'data')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports')
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), '..', 'web', 'report_template.html')
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(os.path.join(DATA_DIR, 'index.json')) as f: idx = json.load(f)
with open(os.path.join(DATA_DIR, 'detail.json')) as f: det = json.load(f)
with open(os.path.join(DATA_DIR, 'sectors.json')) as f: sectors = json.load(f)
with open(os.path.join(DATA_DIR, 'history.json')) as f: hist = json.load(f)
with open(os.path.join(DATA_DIR, 'run_status.json')) as f: status = json.load(f)

trade_date = idx['trade_date']
score = idx['composite_score']
level = idx['level']
ind = det.get('indicators', {})
now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
td_clean = trade_date.replace('-', '')

DIM_ORDER = ['valuation','fund','sentiment','structure']
DIM_LABELS = {'valuation':'估值','fund':'资金','sentiment':'情绪','structure':'结构'}
DIM_WEIGHTS = {'valuation':'40%','fund':'30%','sentiment':'20%','structure':'10%'}
LEVEL_EMOJI = {'red':'🔴','yellow':'🟡','green':'🟢'}
LEVEL_CN = {'red':'红色预警','yellow':'黄色警惕','green':'绿色安全'}
LEVEL_LABEL = {'red':'牛市过热','yellow':'中性偏热','green':'安全低估'}
LEVEL_COLOR_HEX = {'red':'#ff4d4f','yellow':'#faad14','green':'#52c41a'}
LEVEL_BG_HEX = {'red':'#2a1215','yellow':'#2a2010','green':'#0f2a12'}
level_emoji = LEVEL_EMOJI.get(level,'⚪')
level_cn = LEVEL_CN.get(level,'未知')
level_label = LEVEL_LABEL.get(level,'')
level_color = LEVEL_COLOR_HEX.get(level,'#888')
level_bg = LEVEL_BG_HEX.get(level,'#0a0e17')
DIM_COLORS = {'valuation':'#1890ff','fund':'#52c41a','sentiment':'#faad14','structure':'#eb2f96'}
dim_scores = {k: v['score'] for k, v in idx['dimensions'].items()}
prev_score, prev_date = None, None
if len(hist) >= 2:
    prev_score = hist[-2]['composite_score']
    prev_date = hist[-2]['trade_date']
prev_dims = {}
if len(hist) >= 2 and 'dimensions' in hist[-2]:
    for k, v in hist[-2]['dimensions'].items():
        prev_dims[k] = v.get('score') if isinstance(v, dict) else v
hist_dates = [h['trade_date'][5:] for h in hist]
hist_scores = [h['composite_score'] for h in hist]
dim_hist = {k: [] for k in DIM_ORDER}
for h in hist:
    if 'dimensions' in h:
        for k in DIM_ORDER:
            v = h.get('dimensions', {}).get(k)
            dim_hist[k].append(v.get('score') if isinstance(v, dict) else v)

def score_color(v):
    if v is None: return '#374151'
    return '#ff4d4f' if v >= 70 else ('#faad14' if v >= 40 else '#52c41a')
def score_label(v):
    if v is None: return '—'
    return '偏高' if v >= 70 else ('中性' if v >= 40 else '偏低')
def bar_md(v, w=10):
    if v is None: return '░'*w
    n = int(v/100*w)
    return '█'*n+'░'*(w-n)
def delta_md(cur, prev):
    if prev is None or cur is None: return ''
    d = cur-prev
    a = '↑' if d > 0 else ('↓' if d < 0 else '→')
    return ' %s%.1f' % (a, abs(d))
def dim_delta_str(cur, prev):
    if prev is None or cur is None: return ''
    d = cur-prev
    a = '▲' if d > 0 else ('▼' if d < 0 else '─')
    c = '#ff4d4f' if d > 0 else ('#52c41a' if d < 0 else '#888')
    return ' <span style="color:%s;font-weight:600;font-size:0.7em">%s%.1f</span>' % (c, a, abs(d))

_hl = []
inds = det.get('indicators', {})
# V2 指标评分 (百分位分数, 取自 detail.json indicators)
for _k, _label, _fmt, _note in [
    ('pe', '大盘PE', '%.1f', '分'), ('erp', 'ERP', '%.1f', '分'),
    ('buffett', '巴菲特指标', '%.1f', '分'), ('margin_ratio_v2', '两融余额市值比', '%.1f', '分'),
    ('deposit_ratio', '存款市值比', '%.1f', '分'), ('turnover_m2', '成交额M2比', '%.1f', '分'),
    ('turnover', '换手率', '%.1f', '分'), ('new_high', '创新高占比', '%.1f', '分'),
    ('ma_alignment', 'MA排列比', '%.1f', '分'),
]:
    v = inds.get(_k) if isinstance(inds, dict) else None
    if v is not None: _hl.append((_label, _fmt % v, _note))
# 展示指标 (原始值, 不参与V2计算)
if idx.get('display_up_down_ratio') is not None:
    _hl.append(('涨跌家数比','%.2f'%idx['display_up_down_ratio'],'涨/跌'))
if idx.get('display_limit_up_ratio') is not None:
    _hl.append(('涨停占比','%.2f%%'%(idx['display_limit_up_ratio']*100),'全市场'))
if idx.get('display_limit_ratio') is not None:
    _hl.append(('涨跌停比','%.1f'%idx['display_limit_ratio'],'涨停/跌停'))
if idx.get('display_below_net_rate') is not None:
    _hl.append(('破净率','%.1f%%'%(idx['display_below_net_rate']*100),'全市场'))
# QVIX (展示不计分)
qv = idx.get('qvix_display')
if qv is not None: _hl.append(('QVIX恐慌指数','%.1f'%qv,'展示不计分'))

step_dicts = [v for v in status['steps'].values() if isinstance(v, dict)]
n_ok = sum(1 for v in step_dicts if v.get('status')=='OK')
n_fail = sum(1 for v in step_dicts if v.get('status')=='FAILED')
n_skip = sum(1 for v in step_dicts if v.get('status')=='SKIPPED')

# ── 1. MD ──
md = []
md.append('# %s A股牛市热度指数日报' % level_emoji)
md.append('')
md.append('> 📅 **%s**  ·  🕐 %s  ·  📊 日频  ·  v3.5' % (trade_date, now_str))
md.append('')
md.append('---')
md.append('')
md.append('## 综合热度: %s %.1f — %s' % (level_emoji, score, level_cn))
md.append('')
md.append('```')
md.append('  %s  %.0f/100' % (bar_md(score), score))
md.append('```')
md.append('')
if prev_score is not None:
    d = score-prev_score
    a = '↑' if d > 0 else ('↓' if d < 0 else '→')
    trend = '📈 上升' if d > 0 else ('📉 下降' if d < 0 else '➡️ 持平')
    md.append('**%s 较上一交易日 (%s): %s %.1f 分**' % (trend, prev_date, a, abs(d)))
md.append('')
md.append('---')
md.append('')
md.append('## 📊 六维度拆解')
md.append('')
md.append('| 维度 | 权重 | 得分 | 趋势 | 评估 |')
md.append('|:-----|:----:|-----:|:----:|:----:|')
for k in DIM_ORDER:
    s = dim_scores.get(k)
    ps = prev_dims.get(k)
    if s is None:
        md.append('| %s | %s | — | — | — |' % (DIM_LABELS[k], DIM_WEIGHTS[k]))
    else:
        ev = '🔴 偏高' if s>=70 else ('🟡 中性' if s>=40 else '🟢 偏低')
        trend_cell = delta_md(s,ps).strip() if ps else ''
        md.append('| **%s** | %s | **%.0f** | %s | %s |' % (DIM_LABELS[k], DIM_WEIGHTS[k], s, trend_cell, ev))
md.append('')
md.append('```')
md.append('维度      进度条           得分   趋势')
md.append('─' * 48)
for k in DIM_ORDER:
    s = dim_scores.get(k)
    ps = prev_dims.get(k)
    name = DIM_LABELS[k].ljust(6)
    bar = bar_md(s)
    score_str = '%5.1f' % s if s else '  — '
    trend = delta_md(s,ps).strip() if ps else ''
    md.append('%s  %s  %s  %s' % (name, bar, score_str, trend))
md.append('```')
md.append('')
md.append('### 📌 关键指标')
md.append('')
for name, val, note in _hl:
    md.append('- **%s** `%s` (%s)' % (name, val, note))
md.append('')
md.append('---')
md.append('')
md.append('## 📈 历史走势 (共%d个交易日)' % len(hist))
md.append('')
md.append('| 日期 | 得分 | 状态 |')
md.append('|:-----|-----:|:----:|')
for h in hist[-10:]:
    md.append('| %s | %.1f | %s %s |' % (h['trade_date'], h['composite_score'], LEVEL_EMOJI.get(h['level'],'⚪'), h['level']))
md.append('')
md.append('---')
md.append('')
md.append('## 📊 历史参考')
md.append('')
md.append('| 日期 | 市场状态 | 综合得分 |')
md.append('|:-----|:---------|--------:|')
md.append('| 2015-06-12 | 牛市顶 (上证5178) | 🔴 73.8 |')
md.append('| 2021-02-18 | 牛市顶 (上证3731) | ⚪ 66.2 |')
md.append('| 2024-10-08 | 脉冲顶 (上证3489) | ⚪ 65.1 |')
md.append('| 2018-12-28 | 熊底 (上证2493) | 🟢 28.5 |')
md.append('| **%s** | **★ 当前** | **%s %.1f** |' % (trade_date, level_emoji, score))
md.append('')
md.append('---')
md.append('')
md.append('## ⚙️ 运行状态')
md.append('')
md.append('> %d✅ %d❌ %d⏭️' % (n_ok, n_fail, n_skip))
md.append('')
for sn, sv in status['steps'].items():
    if not isinstance(sv, dict): continue
    ic = '✅' if sv['status']=='OK' else ('⏭️' if sv['status']=='SKIPPED' else '❌')
    md.append('- %s `%s` %s (%.1fs)' % (ic, sn, sv['status'], sv.get('elapsed',0)))
md.append('')
md.append('---')
md.append('')
md.append('> ⚠️ 不构成投资建议，仅供参考')
md.append('> bull-market-heat-index v3.5 · tushare + akshare + 恒生HSAHP')
with open(os.path.join(OUTPUT_DIR,'daily_%s.md'%td_clean),'w',encoding='utf-8') as f: f.write('\n'.join(md))
print('MD saved: daily_%s.md' % td_clean)

# ── 2. HTML ──
if os.path.exists(TEMPLATE_PATH):
    tpl = open(TEMPLATE_PATH, encoding='utf-8').read()
else:
    tpl = '<html><body><pre>{{CONTENT}}</pre></body></html>'

dim_cards = ''
for k in DIM_ORDER:
    s = dim_scores.get(k); dc = score_color(s); pct = min(max(s,0),100) if s else 0
    ps = prev_dims.get(k); sl = score_label(s); d_html = dim_delta_str(s,ps) if ps else ''
    if s is None:
        dim_cards += '<div class="dim-card na"><div class="dim-header"><span class="dim-name">%s</span><span class="dim-weight">%s</span></div><div class="dim-score">—</div><div class="dim-eval">数据暂缺</div></div>' % (DIM_LABELS[k],DIM_WEIGHTS[k])
    else:
        dim_cards += '<div class="dim-card"><div class="dim-header"><span class="dim-name">%s</span><span class="dim-weight">%s</span></div><div class="dim-score" style="color:%s">%.0f<span class="dim-delta">%s</span></div><div class="dim-eval" style="color:%s">%s</div><div class="dim-bar"><div class="dim-bar-fill" style="width:%d%%;background:%s"></div></div></div>' % (DIM_LABELS[k],DIM_WEIGHTS[k],dc,s,d_html,dc,sl,int(pct),dc)

hl_html = ''.join(['<li><span class="hl-name">%s</span><span class="hl-val">%s</span><span class="hl-note">%s</span></li>\n' % (n,v,t) for n,v,t in _hl])

delta_html = ''
if prev_score is not None:
    d=score-prev_score; arrow='▲' if d>0 else ('▼' if d<0 else '─'); dc='#ff4d4f' if d>0 else ('#52c41a' if d<0 else '#888')
    trend_label = '上升' if d>0 else ('下降' if d<0 else '持平')
    delta_html = '<div class="delta">较上一交易日 <span style="color:%s">%s %.1f (%s)</span></div>' % (dc,arrow,abs(d),trend_label)

status_items = ''.join(['<div class="status-item">%s <code>%s</code> <span class="%s">%s</span> <span class="elapsed">%.1fs</span></div>' % ('✅' if v['status']=='OK' else ('⏭️' if v['status']=='SKIPPED' else '❌'),sn,v['status'].lower(),v['status'],v.get('elapsed',0)) for sn,v in status['steps'].items() if isinstance(v, dict)])

dim_hist_js = {k: [round(v,1) if v is not None else None for v in dim_hist[k]] for k in dim_hist}

def rep(s, old, new):
    return s.replace(old, new)

out = tpl
out = rep(out, '__TRADE_DATE__', trade_date)
out = rep(out, '__NOW_STR__', now_str)
out = rep(out, '__LEVEL_COLOR__', level_color)
out = rep(out, '__LEVEL_BG__', level_bg)
out = rep(out, '__LEVEL_EMOJI__', level_emoji)
out = rep(out, '__LEVEL_CN__', level_cn)
out = rep(out, '__LEVEL_LABEL__', level_label)
out = rep(out, '__SCORE__', '%.1f' % score)
out = rep(out, '__SCORE_PCT__', str(min(max(int(score),0),100)))
out = rep(out, '__SCORE_DASH__', str(score*4.4))
out = rep(out, '__DELTA_HTML__', delta_html)
out = rep(out, '__DIM_CARDS__', dim_cards)
out = rep(out, '__HL_HTML__', hl_html)
out = rep(out, '__STATUS_ITEMS__', status_items)
out = rep(out, '__N_OK__', str(n_ok))
out = rep(out, '__N_FAIL__', str(n_fail))
out = rep(out, '__N_SKIP__', str(n_skip))
out = rep(out, '__HIST_DATES__', json.dumps(hist_dates))
out = rep(out, '__HIST_SCORES__', json.dumps(hist_scores))
out = rep(out, '__DIM_COLORS__', json.dumps([DIM_COLORS[k] for k in DIM_ORDER]))
out = rep(out, '__DIM_NAMES__', json.dumps([DIM_LABELS[k] for k in DIM_ORDER]))
out = rep(out, '__DIM_DATA__', json.dumps([dim_hist_js[k] for k in DIM_ORDER]))

with open(os.path.join(OUTPUT_DIR,'daily_%s.html'%td_clean),'w',encoding='utf-8') as f: f.write(out)
print('HTML saved: daily_%s.html' % td_clean)

# ── 3. PNG ──
try:
    from PIL import Image, ImageDraw, ImageFont as IF
    _font_path = next((p for p in ['/System/Library/Fonts/PingFang.ttc','/System/Library/Fonts/Hiragino Sans GB.ttc','/System/Library/Fonts/STHeiti Medium.ttc'] if os.path.exists(p)), None)
    def _f(sz):
        try: return IF.truetype(_font_path, sz)
        except Exception: return IF.load_default()
    C = {'bg':(10,14,23),'card':(17,24,39),'card_hover':(25,32,48),'border':(30,41,59),'border_light':(45,55,72),'text':(201,209,217),'bright':(224,230,237),'dim':(107,114,128),'muted':(55,65,81),'red':(255,77,79),'yellow':(250,173,20),'green':(82,196,26),'accent':(88,166,255)}
    _lc = score_color(score); W,PAD = 880,36; TOTAL_H=2400
    img=Image.new('RGB',(W,TOTAL_H),C['bg']); draw=ImageDraw.Draw(img); y=PAD

    def _txt(s,x,y,color=C['text'],size=14,bold=False):
        font=_f(size+(4 if bold else 0)); draw.text((x,y),s,fill=color,font=font); return y+size+6

    def _txt_center(s,y,color=C['text'],size=14,bold=False):
        font=_f(size+(4 if bold else 0)); bbox=font.getbbox(s); tw=bbox[2]-bbox[0]
        draw.text(((W-tw)//2,y),s,fill=color,font=font); return y+size+6

    def _bar(cx,cy,w,sc,color,h=6):
        draw.rectangle([(cx,cy),(cx+w,cy+h)],fill=C['border'])
        fw=int(w*min(max(sc,0),100)/100)
        if fw>0: draw.rectangle([(cx,cy),(cx+fw,cy+h)],fill=color)

    def _rr(xy,r=6,fill=None,outline=None,width=1):
        draw.rounded_rectangle(xy,radius=r,fill=fill,outline=outline,width=width)

    def _gradient_rect(x1,y1,x2,y2,color_top,color_bottom):
        for yi in range(y1,y2):
            ratio=(yi-y1)/max(y2-y1,1)
            r=int(color_top[0]*(1-ratio)+color_bottom[0]*ratio)
            g=int(color_top[1]*(1-ratio)+color_bottom[1]*ratio)
            b=int(color_top[2]*(1-ratio)+color_bottom[2]*ratio)
            draw.line([(x1,yi),(x2,yi)],fill=(r,g,b))

    # 标题
    _rr([(PAD,y),(W-PAD,y+72)],r=10,fill=C['card'],outline=C['border'])
    t="A股牛市热度指数日报"; _txt_center(t,y+12,C['bright'],20,True)
    m="📅 %s  ·  🕐 %s  ·  📈 日频  ·  v3.5"%(trade_date,now_str); _txt_center(m,y+42,C['dim'],11)
    y+=88

    # 综合得分
    _rr([(PAD,y),(W-PAD,y+200)],r=10,fill=C['card'],outline=C['border'])
    ss="%.1f"%score; _txt_center(ss,y+16,_lc,64,True)
    _txt_center(level_cn,y+90,_lc,16,True)
    if level_label: _txt_center(level_label,y+114,C['dim'],11)
    bar_y=y+134; _bar(PAD+80,bar_y,W-2*PAD-160,score,_lc,8)
    for tick,tx in [(0,'0'),(40,'40'),(65,'65'),(100,'100')]:
        tx_x=PAD+80+int((W-2*PAD-160)*tick/100)-8; draw.text((tx_x,bar_y+12),tx,fill=C['muted'],font=_f(9))
    if prev_score is not None:
        d=score-prev_score; arrow='▲' if d>0 else ('▼' if d<0 else '─'); dc=C['red'] if d>0 else C['green']
        ds="较上一交易日: %s %.1f"%(arrow,abs(d)); _txt_center(ds,y+156,dc,11)
    y+=216

    # 六维度拆解
    _txt("📊 六维度拆解",PAD,y,C['bright'],16,True); y+=8
    cw=(W-2*PAD-5*8)//6; ch=96; cy0=y
    for i,dk in enumerate(DIM_ORDER):
        s=dim_scores.get(dk); sc=s if s else 0; scol=score_color(s)
        cx=PAD+i*(cw+8); _rr([(cx,cy0),(cx+cw,cy0+ch)],r=8,fill=C['card'],outline=C['border'])
        draw.text((cx+8,cy0+8),DIM_LABELS[dk],fill=C['dim'],font=_f(11))
        bbox=_f(9).getbbox(DIM_WEIGHTS[dk]); draw.text((cx+cw-(bbox[2]-bbox[0])-8,cy0+10),DIM_WEIGHTS[dk],fill=C['muted'],font=_f(9))
        ss2="%.0f"%sc if s else "—"; bbox=_f(32).getbbox(ss2); draw.text((cx+(cw-(bbox[2]-bbox[0]))//2,cy0+28),ss2,fill=scol,font=_f(32))
        sl=score_label(s); bbox=_f(10).getbbox(sl); draw.text((cx+(cw-(bbox[2]-bbox[0]))//2,cy0+66),sl,fill=scol,font=_f(10))
        _bar(cx+8,cy0+82,cw-16,sc,scol,4)
    y=cy0+ch+24

    # 关键指标
    _txt("📌 关键指标",PAD,y,C['bright'],16,True); y+=8
    col_w=(W-2*PAD-12)//2
    for idx2,(name,val,note) in enumerate(_hl[:10]):
        col=idx2%2; row=idx2//2; mx=PAD+col*(col_w+12); my=y+row*36
        _rr([(mx,my),(mx+col_w,my+32)],r=4,fill=C['card'],outline=C['border'])
        draw.text((mx+12,my+8),name,fill=C['dim'],font=_f(11))
        draw.text((mx+110,my+8),val,fill=C['bright'],font=_f(12))
        draw.text((mx+col_w-70,my+8),note,fill=C['muted'],font=_f(10))
    y+=((len(_hl[:10])+1)//2)*36+16

    # 历史参考
    _txt("📊 历史参考",PAD,y,C['bright'],14,True); y+=8
    refs=[("2015-06-12","牛市顶 (上证5178)","73.8",C['red']),("2021-02-18","牛市顶 (上证3731)","66.2",C['dim']),
          ("2024-10-08","脉冲顶 (上证3489)","65.1",C['dim']),("2018-12-28","熊底 (上证2493)","28.5",C['green']),
          (trade_date,"★ 当前","%.1f"%score,_lc)]
    for dt2,st2,sc2,sc2c in refs:
        bg = C['card'] if dt2!=trade_date else C['border']
        border = C['border'] if dt2!=trade_date else C['accent']
        _rr([(PAD,y),(W-PAD,y+28)],r=4,fill=bg,outline=border,width=1 if dt2==trade_date else 0)
        draw.text((PAD+12,y+6),dt2,fill=sc2c,font=_f(12))
        draw.text((PAD+120,y+6),st2,fill=C['text'],font=_f(12))
        bbox=_f(12).getbbox(sc2); draw.text((W-PAD-12-(bbox[2]-bbox[0]),y+6),sc2,fill=sc2c,font=_f(12)); y+=32
    y+=16

    # 运行状态
    draw.line([(PAD,y+10),(W-PAD,y+10)],fill=C['border'],width=1); y+=20
    ft="⚙️ 运行状态: %d OK / %d FAILED / %d SKIPPED"%(n_ok,n_fail,n_skip)
    _txt_center(ft,y,C['muted'],10); y+=20
    wt="⚠️ 不构成投资建议，仅供参考  ·  v3.5"
    _txt_center(wt,y,C['muted'],10); y+=24

    img.crop((0,0,W,y+12)).save(os.path.join(OUTPUT_DIR,'daily_%s.png'%td_clean),'PNG',optimize=True)
    print('PNG saved: daily_%s.png' % td_clean)
except ImportError:
    print('Pillow not installed, skipping PNG')

print('Done: daily_%s.{md,html,png}' % td_clean)
