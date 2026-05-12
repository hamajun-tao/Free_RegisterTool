"""统计 hotmailcloud.cloud 成功账号的时间分布，找出 fraud_guard 冷却窗口"""
import sqlite3
import os
import collections
from datetime import datetime

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "account_manager.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

cur.execute(
    "SELECT created_at FROM accounts "
    "WHERE platform='chatgpt' AND email LIKE '%hotmailcloud.cloud' AND status='registered' "
    "ORDER BY created_at DESC LIMIT 200"
)
rows = cur.fetchall()

by_hour = collections.Counter()
by_day = collections.Counter()
for r in rows:
    s = r["created_at"]
    try:
        dt = datetime.fromisoformat(s.split(".")[0])
    except Exception:
        continue
    by_hour[dt.hour] += 1
    by_day[dt.strftime("%Y-%m-%d")] += 1

print("=== 最近 200 个成功账号按小时分布（UTC）===")
for h in sorted(by_hour):
    bar = "#" * by_hour[h]
    print(f"  {h:02d}:00  {by_hour[h]:3d}  {bar}")

print("\n=== 按日期分布 ===")
for d in sorted(by_day, reverse=True)[:14]:
    print(f"  {d}  {by_day[d]:3d}  {'#' * by_day[d]}")

# 最近 5 个的具体时间
print("\n=== 最近 5 个成功账号时间 ===")
for r in rows[:5]:
    print(f"  {r['created_at']}")
