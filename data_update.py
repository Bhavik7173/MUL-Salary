import sqlite3

conn = sqlite3.connect("work_log.db")
cursor = conn.cursor()

cursor.execute("ALTER TABLE daily_records ADD COLUMN user_id INTEGER")
conn.commit()
conn.close()
