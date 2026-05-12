import sqlite3
import os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "account_manager.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.execute("SELECT url,region,success_count,fail_count,is_active,last_checked FROM proxies ORDER BY success_count DESC LIMIT 20")
rows = cur.fetchall()
for r in rows:
    print(f'{r["success_count"]}/{r["fail_count"]}  active={r["is_active"]}  {r["url"]}  ({r["region"]})')
