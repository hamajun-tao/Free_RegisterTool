"""
查 OAuth 成功账号（有 refresh_token）使用的注册代理节点
"""
import sqlite3
import json
import os
import sys
import collections

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "account_manager.db")

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("TABLES:", tables)

if "accounts" not in tables:
    sys.exit("no accounts table")

cur.execute("PRAGMA table_info(accounts)")
cols = [r[1] for r in cur.fetchall()]
print("COLS:", cols)

cur.execute(
    "SELECT id, email, status, token, extra_json, created_at "
    "FROM accounts WHERE platform='chatgpt' AND email LIKE '%hotmailcloud.cloud' "
    "ORDER BY created_at DESC LIMIT 500"
)
rows = cur.fetchall()
print(f"\n=== hotmailcloud.cloud 账号总数 = {len(rows)} ===\n")

success_proxies = collections.Counter()
fail_proxies = collections.Counter()

for r in rows:
    extra = {}
    try:
        extra = json.loads(r["extra_json"] or "{}")
    except Exception:
        pass

    refresh_token = (
        extra.get("refresh_token")
        or extra.get("oai_refresh_token")
        or ""
    )
    has_rt = bool(str(refresh_token).strip())
    proxy = (
        extra.get("proxy")
        or extra.get("proxy_url")
        or extra.get("registration_proxy")
        or extra.get("worker_proxy")
        or ""
    )

    if has_rt:
        tag = "[OK ]"
        success_proxies[proxy] += 1
    else:
        tag = "[FAIL]"
        fail_proxies[proxy] += 1

    print(f"{tag} id={r['id']} status={r['status']} proxy={proxy} email={r['email']}")

print("\n=== OAuth 成功（有 refresh_token）账号代理统计 ===")
for k, v in success_proxies.most_common():
    print(f"  {v} 次  {k or '<NO_PROXY>'}")

print("\n=== 失败账号代理统计（仅供对比）===")
for k, v in fail_proxies.most_common(20):
    print(f"  {v} 次  {k or '<NO_PROXY>'}")
