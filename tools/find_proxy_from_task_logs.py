import sqlite3
import json
import os
import collections

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "account_manager.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

# 查最近成功的 hotmailcloud.cloud 任务日志，里面通常有 proxy_url
cur.execute(
    "SELECT id, email, status, detail_json, created_at FROM task_logs "
    "WHERE platform='chatgpt' AND email LIKE '%hotmailcloud.cloud' AND status='success' "
    "ORDER BY created_at DESC LIMIT 30"
)
rows = cur.fetchall()
print(f"=== task_logs 成功记录数 = {len(rows)} ===\n")

proxy_counter = collections.Counter()
for r in rows:
    try:
        detail = json.loads(r["detail_json"] or "{}")
    except Exception:
        detail = {}
    proxy = detail.get("proxy_url") or detail.get("proxy") or ""
    if proxy:
        proxy_counter[proxy] += 1
    print(f"id={r['id']} email={r['email']} proxy={proxy}")

print("\n=== 成功任务代理统计 ===")
for p, cnt in proxy_counter.most_common():
    print(f"  {cnt} 次  {p}")
