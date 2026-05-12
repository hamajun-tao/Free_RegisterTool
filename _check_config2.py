import sqlite3, os

db_path = os.path.join(os.path.dirname(__file__), 'account_manager.db')
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT key, CASE WHEN key LIKE '%password%' OR key LIKE '%secret%' OR key LIKE '%token%' OR key LIKE '%key%' THEN '[HIDDEN]' ELSE value END FROM configs ORDER BY key")
rows = cur.fetchall()
for k, v in rows:
    if v:
        print(f"  {k} = {v}")
conn.close()
