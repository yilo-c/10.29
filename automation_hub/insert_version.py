import sqlite3

conn = sqlite3.connect('data/automation_hub.sqlite3')
cursor = conn.cursor()
cursor.execute("INSERT INTO alembic_version (version_num) VALUES ('8d9806c4a3a1')")
conn.commit()
conn.close()
print("Version inserted successfully")