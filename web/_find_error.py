import re, os

with open('web/app.html', 'r') as f:
    html = f.read()

m = re.search(r'<script>([\s\S]+?)</script>', html)
js = m.group(1)

# Write as ES module and check
with open('/tmp/_app_check.mjs', 'w') as f:
    f.write(js)

ret = os.system('node --check /tmp/_app_check.mjs 2>&1')
print('Exit code:', ret)
