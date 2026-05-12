import sqlite3
import os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "account_manager.db")
con = sqlite3.connect(DB)
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("TABLES:", tables)
for t in tables:
    cur.execute(f"PRAGMA table_info({t})")
    cols = [r[1] for r in cur.fetchall()]
    print(f"  {t}: {cols}")
