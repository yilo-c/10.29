#!/usr/bin/env python3
import sqlite3
import secrets
import hashlib
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "automation_hub.sqlite3"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

raw = secrets.token_urlsafe(32)
hash_hex = hashlib.sha256(raw.encode('utf-8')).hexdigest()
user_id = f"u-{uuid.uuid4()}"

conn = sqlite3.connect(str(DB_PATH))
try:
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("INSERT INTO users(id, name, created_at) VALUES(?,?,?)", (user_id, 'cli-generated', now_iso()))
    conn.execute(
        "INSERT INTO api_tokens(id, user_id, token_hash, scopes, created_at) VALUES(?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, hash_hex, json.dumps(['*']), now_iso()),
    )
    conn.commit()
    print(raw)
finally:
    conn.close()
