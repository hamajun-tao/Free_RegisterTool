import sqlite3, datetime, os
DB = os.path.join(os.path.dirname(__file__), "account_manager.db")
c = sqlite3.connect(DB)
cur = c.cursor()
today = datetime.datetime.now().strftime("%Y-%m-%d")

cur.execute("SELECT COUNT(*) FROM accounts WHERE platform='chatgpt'")
print("total chatgpt accounts:", cur.fetchone()[0])

cur.execute("SELECT COUNT(*) FROM accounts WHERE platform='chatgpt' AND DATE(created_at)=?", (today,))
print(f"today ({today}) registered:", cur.fetchone()[0])

cur.execute("""
    SELECT COUNT(*) FROM accounts 
    WHERE platform='chatgpt' AND created_at >= datetime('now', '-2 hour')
""")
print("last 2h registered:", cur.fetchone()[0])

cur.execute("""
    SELECT email, status, created_at FROM accounts 
    WHERE platform='chatgpt' 
    ORDER BY created_at DESC LIMIT 15
""")
print("--- last 15 chatgpt accounts ---")
for r in cur.fetchall():
    print(r)
