import sqlite3

conn = sqlite3.connect('data/automation_hub.sqlite3')
cursor = conn.cursor()
cursor.execute("SELECT * FROM alembic_version")
version = cursor.fetchall()
print('Alembic version:', version)
conn.close()