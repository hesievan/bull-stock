#!/usr/bin/env python3
"""Fix fetch_ah_premium retry timing in fetcher.py"""
import re

fetcher_path = 'src/data/fetcher.py'
with open(fetcher_path, 'r') as f:
    content = f.read()

old = '''    klines = []
    for attempt in range(5):
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "30",
                 "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                 "-H", "Referer: https://quote.eastmoney.com/",
                 url],
                capture_output=True, text=True,
            )
            if result.returncode != 0 or not result.stdout.strip():
                raise ConnectionError(f"curl rc={result.returncode}")
            data = _json.loads(result.stdout)
            klines = (data.get("data") or {}).get("klines") or []
            if klines:
                break
            raise ValueError("空数据")
        except Exception as e:
            if attempt < 4:
                wait = 20 if attempt < 2 else 40
                logger.warning("fetch_ah_premium attempt %d/%d: %s", attempt + 1, 5, str(e)[:80])
                time.sleep(wait)
            else:
                logger.error("fetch_ah_premium failed: %s", str(e)[:80])
                return pd.DataFrame()'''

new = '''    klines = []
    for attempt in range(5):
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "15",
                 "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                 "-H", "Referer: https://quote.eastmoney.com/",
                 url],
                capture_output=True, text=True, timeout=20,
            )
            if result.returncode != 0 or not result.stdout.strip():
                raise ConnectionError(f"curl rc={result.returncode}")
            data = _json.loads(result.stdout)
            klines = (data.get("data") or {}).get("klines") or []
            if klines:
                break
            raise ValueError("空数据")
        except Exception as e:
            if attempt < 4:
                # rc=52/7: 服务器空应答/拒绝, 快速重试 3-12s; 其他错误: 长等
                is_fast = ("rc=52" in str(e)) or ("rc=7" in str(e))
                wait = (3 + attempt * 3) if is_fast else (20 + attempt * 10)
                wait = min(wait, 15)
                logger.warning("fetch_ah_premium attempt %d/%d: %s (wait %ds)", attempt + 1, 5, str(e)[:60], wait)
                time.sleep(wait)
            else:
                logger.error("fetch_ah_premium failed after 5 attempts: %s", str(e)[:80])
                return pd.DataFrame()'''

if old in content:
    content = content.replace(old, new)
    with open(fetcher_path, 'w') as f:
        f.write(content)
    print('OK - replaced')
else:
    print('NOT FOUND - checking...')
    # 找到实际内容
    idx = content.find('klines = []')
    if idx >= 0:
        print(repr(content[idx:idx+200]))
