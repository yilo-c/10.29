import sqlite3
from pathlib import Path
p = Path(__file__).resolve().parent.parent / 'data' / 'automation_hub.sqlite3'
print('DB:', p)
if not p.exists():
    print('DB not found')
else:
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        rows = cur.execute('SELECT id,token_hash,scopes,created_at,expires_at,revoked_at FROM api_tokens').fetchall()
        print('tokens:', len(rows))
        for r in rows:
            print(dict(r))
    except Exception as e:
        print('error', e)
    finally:
        conn.close()
