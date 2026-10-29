# -*- coding: utf-8 -*-
"""诊断：检查 local_storage.db 中 audit_trail 等键的体积（临时脚本）。"""
import sqlite3

con = sqlite3.connect(r"data\data\local_storage.db")
cur = con.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("tables:", tables)
for tbl in tables:
    cols = [c[1] for c in cur.execute(f"PRAGMA table_info({tbl})").fetchall()]
    key_col, val_col = cols[0], cols[1]
    row = cur.execute(
        f"SELECT length({val_col}) FROM {tbl} WHERE {key_col}=?", ("audit_trail",)
    ).fetchone()
    if row and row[0]:
        print(f"audit_trail = {row[0]/1024/1024:.1f} MB")
    top = cur.execute(
        f"SELECT {key_col}, length({val_col}) as L FROM {tbl} ORDER BY L DESC LIMIT 10"
    ).fetchall()
    print(f"--- {tbl} top keys ---")
    for k, L in top:
        print(f"  {L/1024/1024:8.2f} MB  {k}")
