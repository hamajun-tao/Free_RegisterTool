"""
1) 恢复 smsbower_max_price = 0.15
2) 禁用 proxies 表中成功率低于 25% 的节点
   - 计算 success_rate = success / (success + fail)
   - 当总样本 >= 5 且 rate < 0.25 → is_active = 0
   - 总样本 < 5 的节点保留观察
"""
import os
import sys
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.config_store import config_store  # noqa: E402

# Step 1: 恢复 max_price
config_store.set("smsbower_max_price", "0.15")
print(f"[OK] smsbower_max_price -> {config_store.get('smsbower_max_price')!r}")

# Step 2: 禁用低成功率代理
DB = os.path.join(ROOT, "account_manager.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.execute("SELECT id, url, success_count, fail_count, is_active FROM proxies")
rows = cur.fetchall()
disabled = []
kept = []
for r in rows:
    total = r["success_count"] + r["fail_count"]
    rate = r["success_count"] / total if total else 0.0
    # 只对样本够多的节点判定（>=5 次总样本），样本不足保留观察
    if total >= 5 and rate < 0.25:
        cur.execute("UPDATE proxies SET is_active=0 WHERE id=?", (r["id"],))
        disabled.append((r["url"], r["success_count"], r["fail_count"], round(rate, 3)))
    elif r["is_active"] == 0 and (total < 5 or rate >= 0.25):
        # 恢复曾经被自动禁用但样本不足/成功率回升的节点
        cur.execute("UPDATE proxies SET is_active=1 WHERE id=?", (r["id"],))
        kept.append((r["url"], r["success_count"], r["fail_count"], round(rate, 3), "REACTIVATED"))
    else:
        if r["is_active"]:
            kept.append((r["url"], r["success_count"], r["fail_count"], round(rate, 3), "kept"))

con.commit()

print(f"\n[DISABLED {len(disabled)} 个低成功率节点 (<25%, 样本>=5)]")
for u, s, f, r in disabled:
    print(f"  {s}/{f} rate={r}  {u}")

print(f"\n[KEPT {len(kept)} 个 active 节点]")
for tup in kept:
    print(f"  {tup}")

cur.execute("SELECT COUNT(*) FROM proxies WHERE is_active=1")
print(f"\n总 active 节点数: {cur.fetchone()[0]}")
con.close()
