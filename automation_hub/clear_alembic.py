import sqlite3

conn = sqlite3.connect('data/automation_hub.sqlite3')
cursor = conn.cursor()
cursor.execute("DELETE FROM alembic_version")
conn.commit()
conn.close()
print("Alembic version cleared")
