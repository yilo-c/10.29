import sqlite3

conn = sqlite3.connect('data/automation_hub.sqlite3')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('approval_requests', 'execution_authorizations', 'tools', 'tool_versions', 'tool_runs', 'run_events', 'run_artifacts')")
tables = cursor.fetchall()
print('Existing tables:', tables)
conn.close()