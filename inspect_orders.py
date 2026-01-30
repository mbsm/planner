import sqlite3
from pathlib import Path

db_path = Path("db/foundryplan.db")
try:
    con = sqlite3.connect(db_path)
    cursor = con.cursor()
    cursor.execute("PRAGMA table_info(orders)")
    columns = cursor.fetchall()
    print("Columns in orders:")
    for col in columns:
        print(col)
        
    cursor.execute("PRAGMA table_info(parts)")
    columns = cursor.fetchall()
    print("\nColumns in parts:")
    for col in columns:
        print(col)

    cursor.execute("PRAGMA table_info(material_master)")
    columns = cursor.fetchall()
    print("\nColumns in material_master:")
    for col in columns:
        print(col)
        
    con.close()
    
except Exception as e:
    print("Error:", e)
