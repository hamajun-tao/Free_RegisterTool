"""
sub2api 批量测试 active 账号工具

复用 auto_reg-main 的 config_store 里已配置的 sub2api_api_url + sub2api_api_key，
不需要任何额外参数；用户双击 .bat 即可触发。

流程：
  1. 自动读 config_store 拿 sub2api 后台地址 + admin token
  2. GET /api/v1/admin/accounts 拉 active openai 账号列表
  3. 并发对每个账号 POST /api/v1/admin/accounts/{id}/test 跑真实 OpenAI API 测试
  4. 解析 SSE 响应：success / failed / unknown 分类
  5. 输出 sub2api_dead_accounts.csv 报告
  6. 询问是否批量调 DELETE 接口（软删除），用户输入 yes 才执行
"""

from __future__ import annotations

import csv
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

# 把 auto_reg-main 项目根加到 sys.path（让 import core.config_store 能 work）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import requests  # type: ignore

# ── 关键：禁用代理透传（auto_reg-main 配置了 HTTP_PROXY 用于 OpenAI 注册，
# 但 sub2api 是本地服务，必须直连，否则代理会把请求拦下并返回 401/超时）──
_session = requests.Session()
_session.trust_env = False   # 不读环境变量里的 HTTP_PROXY / HTTPS_PROXY
_session.proxies = {}

# ── 可调参数 ──
PLATFORM        = "openai"     # 只测 openai 账号；要测所有平台改成 ""
STATUS          = "active"
TEST_MODEL_ID   = ""           # 空字符串 → sub2api 默认用 gpt-5.4
TEST_PROMPT     = "hi"
MAX_PARALLEL    = 8            # 并发数；OpenAI 限流时降到 4
SINGLE_TIMEOUT  = 35           # 单号测试超时（秒）
PAGE_SIZE       = 100
# 失败但属于"临时性错误"的关键字，不会被列入死号删除清单
TRANSIENT_KEYWORDS = (
    "rate limit", "rate_limit", "429",
    "timeout", "timed out",
    "500", "502", "503", "504",
    "internal server error", "bad gateway",
    "service unavailable", "gateway timeout",
    "context deadline exceeded",
    "connection refused", "connection reset",
)


def _read_config() -> tuple[str, str]:
    """读 config_store 的 sub2api_api_url + sub2api_api_key"""
    try:
        from core.config_store import config_store  # type: ignore
    except Exception as exc:
        print(f"[ERROR] 无法导入 config_store: {exc}")
        print("        请确保此脚本放在 auto_reg-main/tools/ 目录下，且 auto_reg-main 项目结构完整")
        sys.exit(1)
    base_url = str(config_store.get("sub2api_api_url") or "").strip().rstrip("/")
    token = str(config_store.get("sub2api_api_key") or "").strip()
    if not base_url:
        print("[ERROR] config_store 里没有 sub2api_api_url，请先在 auto_reg-main 后台 → 全局配置里设置")
        sys.exit(1)
    if not token:
        print("[ERROR] config_store 里没有 sub2api_api_key，请先在 auto_reg-main 后台 → 全局配置里设置")
        sys.exit(1)
    return base_url, token


def _list_accounts(base_url: str, token: str) -> list[dict]:
    """分页拉取 status='active' platform='openai' 的账号"""
    headers = {"Authorization": f"Bearer {token}"}
    accounts: list[dict] = []
    page = 1
    while True:
        params = {
            "page": page,
            "page_size": PAGE_SIZE,
            "status": STATUS,
        }
        if PLATFORM:
            params["platform"] = PLATFORM
        try:
            r = _session.get(
                f"{base_url}/api/v1/admin/accounts",
                params=params,
                headers=headers,
                timeout=30,
            )
        except Exception as exc:
            print(f"[ERROR] 请求 sub2api 失败: {exc}")
            sys.exit(1)
        if r.status_code == 401:
            print("[ERROR] sub2api 返回 401 未授权，sub2api_api_key 可能已过期或无效")
            sys.exit(1)
        if r.status_code != 200:
            print(f"[ERROR] sub2api 返回 {r.status_code}: {r.text[:200]}")
            sys.exit(1)
        data = r.json()
        # sub2api 标准响应：{"code":0,"message":"success","data":{"items":[...],"total":N}}
        # 兼容多种历史格式
        payload = data.get("data") if isinstance(data, dict) else None
        if isinstance(payload, dict):
            items = payload.get("items") or payload.get("accounts") or []
        elif isinstance(payload, list):
            items = payload
        elif isinstance(data, dict):
            items = data.get("items") or data.get("accounts") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        # 校验是 list of dict
        items = [x for x in items if isinstance(x, dict)]
        if not items:
            break
        accounts.extend(items)
        if len(items) < PAGE_SIZE:
            break
        page += 1
        if page > 200:
            break
    return accounts


def _classify_sse(text: str) -> tuple[str, str]:
    """解析 SSE 响应文本，返回 (state, error_message)"""
    if not text:
        return "unknown", "empty response"
    if '"type":"test_complete"' in text and '"success":true' in text:
        return "ok", ""
    if '"type":"error"' in text or '"error"' in text:
        m = re.search(r'"error"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text)
        if m:
            err = m.group(1).replace("\\\"", '"').replace("\\n", " ").strip()
            return "fail", err
        return "fail", text[:300].replace("\n", " ")
    return "unknown", text[:300].replace("\n", " ")


def _is_transient(err: str) -> bool:
    low = (err or "").lower()
    return any(k in low for k in TRANSIENT_KEYWORDS)


def _test_one(base_url: str, token: str, acct: dict) -> dict:
    acct_id = acct.get("id")
    name = acct.get("name") or ""
    url = f"{base_url}/api/v1/admin/accounts/{acct_id}/test"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    body = {"model_id": TEST_MODEL_ID, "prompt": TEST_PROMPT}
    start = time.time()
    try:
        r = _session.post(url, json=body, headers=headers, timeout=SINGLE_TIMEOUT)
        state, err = _classify_sse(r.text)
    except requests.Timeout:
        state, err = "fail", f"client timeout ({SINGLE_TIMEOUT}s)"
    except Exception as exc:
        state, err = "fail", f"network: {exc}"
    return {
        "id": acct_id,
        "name": name,
        "state": state,
        "error": err,
        "transient": _is_transient(err),
        "seconds": round(time.time() - start, 1),
    }


def _delete_accounts(base_url: str, token: str, ids: list[int]) -> tuple[int, int]:
    """通过 DELETE /api/v1/admin/accounts/:id 批量软删除（sub2api 默认走 SoftDelete）"""
    headers = {"Authorization": f"Bearer {token}"}
    ok = 0
    fail = 0
    for i, acct_id in enumerate(ids, 1):
        try:
            r = _session.delete(
                f"{base_url}/api/v1/admin/accounts/{acct_id}",
                headers=headers,
                timeout=15,
            )
            if 200 <= r.status_code < 300:
                ok += 1
            else:
                fail += 1
                print(f"  [{i}] DELETE id={acct_id} 失败: {r.status_code} {r.text[:80]}")
        except Exception as exc:
            fail += 1
            print(f"  [{i}] DELETE id={acct_id} 异常: {exc}")
    return ok, fail


def main() -> None:
    print("==> sub2api 批量测试启动")
    base_url, token = _read_config()
    print(f"    后台:     {base_url}")
    print(f"    平台:     {PLATFORM or '<all>'}")
    print(f"    并发:     {MAX_PARALLEL}")
    print(f"    单号超时: {SINGLE_TIMEOUT}s")

    print("\n[1/4] 拉取账号列表...")
    accounts = _list_accounts(base_url, token)
    if not accounts:
        print("    没有匹配的账号，退出")
        return
    print(f"    共 {len(accounts)} 个账号")

    print(f"\n[2/4] 开始测试...")
    results: list[dict] = []
    done_count = 0
    progress_every = max(1, len(accounts) // 20)
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = [pool.submit(_test_one, base_url, token, a) for a in accounts]
        for fut in as_completed(futures):
            results.append(fut.result())
            done_count += 1
            if done_count % progress_every == 0 or done_count == len(accounts):
                ok_cnt = sum(1 for r in results if r["state"] == "ok")
                fail_cnt = sum(1 for r in results if r["state"] == "fail")
                print(f"    进度 {done_count}/{len(accounts)}  ok={ok_cnt} fail={fail_cnt}")

    ok_list = [r for r in results if r["state"] == "ok"]
    fail_permanent = [r for r in results if r["state"] == "fail" and not r["transient"]]
    fail_transient = [r for r in results if r["state"] == "fail" and r["transient"]]
    unknown_list = [r for r in results if r["state"] == "unknown"]

    print("\n[3/4] 结果汇总：")
    print(f"    ✓ 成功（保留）         : {len(ok_list)}")
    print(f"    ✗ 永久失败（建议删除） : {len(fail_permanent)}")
    print(f"    ⏳ 临时失败（保留）    : {len(fail_transient)} （限流/超时/上游 5xx）")
    print(f"    ? 状态未知            : {len(unknown_list)}")

    out_dir = os.path.join(_ROOT, "tools", "out")
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "sub2api_dead_accounts.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "state", "transient(临时性)", "error", "seconds"])
        for r in fail_permanent + fail_transient + unknown_list:
            w.writerow([
                r["id"], r["name"], r["state"],
                "yes" if r["transient"] else "no",
                (r["error"] or "")[:500],
                r["seconds"],
            ])
    print(f"\n    详细报告：{csv_path}")

    if not fail_permanent:
        print("\n没有需要删除的死号 ✓")
        return

    print(f"\n[4/4] 是否批量软删除 {len(fail_permanent)} 个永久失败账号？")
    print("       （只删 401/403/404 等永久错误，不删 429 限流和超时）")
    print("       软删除可恢复：UPDATE accounts SET deleted_at=NULL WHERE id IN (...)")
    try:
        ans = input("       输入 yes 确认 / 其他键取消: ").strip().lower()
    except EOFError:
        ans = ""
    if ans != "yes":
        print("       已取消（账号未删除，请用 sub2api_dead_accounts.csv 自行决定）")
        return

    print("\n开始批量删除...")
    ok, failed = _delete_accounts(base_url, token, [r["id"] for r in fail_permanent])
    print(f"\n完成：成功删除 {ok} 个，失败 {failed} 个")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户中断")
        sys.exit(130)
