import sqlite3
import json
import os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "account_manager.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.execute(
    "SELECT id, email, status, created_at, extra_json FROM accounts "
    "WHERE platform='chatgpt' AND email LIKE '%hotmailcloud.cloud' AND status='registered' "
    "ORDER BY created_at DESC LIMIT 3"
)
for r in cur.fetchall():
    print(f"=== id={r['id']} email={r['email']} status={r['status']} created_at={r['created_at']} ===")
    try:
        data = json.loads(r["extra_json"] or "{}")
        # 只打印顶层 key 和 proxy 相关
        print("TOP KEYS:", list(data.keys()))
        for k in data.keys():
            v = data[k]
            sv = str(v)
            if len(sv) > 200:
                sv = sv[:200] + "..."
            print(f"  {k} = {sv}")
    except Exception as e:
        print("PARSE ERROR:", e)
        print(r["extra_json"][:500])
    print()
