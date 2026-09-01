#!/usr/bin/env python3
"""CI 辅助: 从 GitHub Actions API 输出本次 run 中结论为 failure 的步骤名。

供 .github/workflows/daily.yml 通知步骤调用:
    FAILED_STEPS=$(python3 scripts/ci_failed_steps.py)

设计:
- 独立脚本避免 YAML block 内嵌 python 导致缩进/引号问题 (2026-09-01 踩坑)
- 尽力而为: 任何异常 (网络/无 token/JSON 异常) 都静默输出空串, 不阻断通知步骤
- 被 `|| echo skipped` 吞掉的软失败在 GitHub 侧仍是 success, 不在此列
  (软失败靠 run_daily 的 data_quality 与数据新鲜度在通知正文体现)

依赖环境变量 (GitHub Actions 自动注入): GH_TOKEN / GITHUB_REPOSITORY / GITHUB_RUN_ID
"""

import json
import os
import sys
import urllib.request


def main() -> None:
    token = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not (token and repo and run_id):
        sys.exit(0)

    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "ci-failed-steps",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except Exception:
        sys.exit(0)

    failed = []
    for job in data.get("jobs", []):
        for step in job.get("steps", []):
            if step.get("conclusion") == "failure":
                failed.append(step.get("name", "?"))
    print("; ".join(failed))


if __name__ == "__main__":
    main()
