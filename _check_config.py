import sqlite3, json, sys, os

db_path = os.path.join(os.path.dirname(__file__), 'account_manager.db')
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("tables:", tables)

# 找配置表
for t in tables:
    if 'config' in t.lower() or 'setting' in t.lower() or 'meta' in t.lower():
        print(f"\n=== {t} ===")
        try:
            cur.execute(f"SELECT * FROM {t} LIMIT 30")
            rows = cur.fetchall()
            for row in rows:
                print(row)
        except Exception as e:
            print(f"  error: {e}")

conn.close()
