#!/usr/bin/env python3
"""Reproduce exactly what run_daily.py Step 9 (S9_notify) would generate,
bypassing the internal debounce so the text is always produced (user
explicitly requested delivery). Then print the Feishu notification text.
"""
import os, sys, json, inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "data")

import src.output.json_writer as jw

# 1) Neutralize the _should_notify gate so the message body is always built.
jw._should_notify = lambda *a, **k: True

# 2) Re-define build_feishu_notification with the FINAL debounce suppression
#    removed (so a "stable" event still returns the text). Everything else
#    (dimension bars, sectors, highlights, data-quality) is identical to the
#    original function.
src = inspect.getsource(jw.build_feishu_notification)
block = (
    '    # 防抖期间不发通知\n'
    '    if event in ("pending_red", "pending_recover", "stable"):\n'
    '        return None  # 不需要推送\n'
)
assert block in src, "debounce block not found; script may have changed"
src = src.replace(block, "    # debounce suppressed by caller request\n")
ns = dict(jw.__dict__)
exec(compile(src, "build_feishu_notification", "exec"), ns)
jw.build_feishu_notification = ns["build_feishu_notification"]

with open(os.path.join(WEB, "detail.json"), encoding="utf-8") as f:
    result = json.load(f)
with open(os.path.join(WEB, "history.json"), encoding="utf-8") as f:
    history = json.load(f)
with open(os.path.join(WEB, "sectors.json"), encoding="utf-8") as f:
    sectors = json.load(f)

result["sectors_top5"] = sectors[:5]

msg = jw.build_feishu_notification(result, history=history)
if msg is None:
    raise SystemExit("build_feishu_notification returned None unexpectedly")

print("===SCORE===", result["composite_score"], result["level"])
print("===DIMS===", {k: v["score"] for k, v in result["dimensions"].items()})
print("===MSG===")
print(msg)
