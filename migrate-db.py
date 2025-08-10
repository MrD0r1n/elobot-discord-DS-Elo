import sqlite3

db_path = 'elo_data.db'  # Adjust if needed

with sqlite3.connect(db_path) as conn:
    c = conn.cursor()

    # Only update rows where `date` is exactly 10 characters (YYYY-MM-DD)
    c.execute('UPDATE match_data SET date = date || " 00:00:00" WHERE length(date) = 10;')
    
    conn.commit()

print("Migration complete. All old rows now have 'YYYY-MM-DD 00:00:00' format.")

