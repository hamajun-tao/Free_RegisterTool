"""触发 1 个 CFWorker 邮箱注册任务并实时打印日志。供 fraud_guard 修复测试使用。

直接通过本地 8000 端口的 HTTP API 调用，绕过浏览器/前端。
JWT 通过 api.auth.create_token() 与后端共享同一密钥。
"""
from __future__ import annotations

import os
import sys
import time

# 让 tools/ 下脚本能 import 同级 backend 模块
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import requests  # noqa: E402

# 与后端共用同一 JWT 密钥（写入同一 config_store）
from api.auth import create_token  # noqa: E402

BASE = "http://127.0.0.1:8000"
SESSION = requests.Session()
SESSION.trust_env = False  # 禁用系统代理（127.0.0.1 直连）
SESSION.proxies = {"http": None, "https": None}  # type: ignore


def main() -> int:
    token = create_token(expire_seconds=3600)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 默认 mail_provider/payment_provider 取自 config_store（由 switch_payment_mode.py 写入）
    # 允许通过环境变量临时覆盖：MAIL_PROVIDER / PAYMENT_PROVIDER
    extra = {}
    mail_provider = os.environ.get("MAIL_PROVIDER", "").strip()
    if mail_provider:
        extra["mail_provider"] = mail_provider
    payment_provider = os.environ.get("PAYMENT_PROVIDER", "").strip()
    if payment_provider:
        extra["payment_provider"] = payment_provider
    body = {
        "platform": "chatgpt",
        "count": 1,
        "concurrency": 1,
        "executor_type": "protocol",
        "extra": extra,
    }
    print(f"[run] POST /api/tasks/register body={body}")
    resp = SESSION.post(f"{BASE}/api/tasks/register", json=body, headers=headers, timeout=30)
    print(f"[run] HTTP {resp.status_code} {resp.text[:200]}")
    resp.raise_for_status()
    task_id = resp.json().get("task_id")
    if not task_id:
        print("[run] no task_id in response", flush=True)
        return 2

    print(f"[run] task_id={task_id}")

    seen = 0
    deadline = time.time() + 60 * 30  # 最多等 30 分钟（GoPay+OAuth+付费 全流程）
    while time.time() < deadline:
        r = SESSION.get(
            f"{BASE}/api/tasks/{task_id}",
            params={"include_logs": "true"},
            headers=headers,
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[run] poll http {r.status_code} {r.text[:200]}")
            time.sleep(2)
            continue
        data = r.json() or {}
        logs = data.get("logs") or []
        offset = int(data.get("log_offset") or 0)
        # logs 是滚动窗口，从 offset 起算的索引。我们只想打印新增的部分
        absolute_start = offset
        # 计算上一次打印到的绝对位置 == seen
        new_start = max(0, seen - absolute_start)
        for line in logs[new_start:]:
            print(line, flush=True)
        seen = absolute_start + len(logs)

        status = str(data.get("status") or "")
        if status in ("done", "stopped", "failed"):
            print(f"[run] task final status={status} success={data.get('success')} errors={len(data.get('errors') or [])}")
            return 0 if data.get("success") else 1
        time.sleep(2)

    print("[run] timeout waiting task completion")
    return 3


if __name__ == "__main__":
    sys.exit(main())
