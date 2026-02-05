"""Check invalid dates."""
import sqlite3

conn = sqlite3.connect('db/foundryplan.db')
cur = conn.cursor()

cnt = cur.execute('SELECT COUNT(*) FROM sap_demolding_snapshot WHERE demolding_date IS NULL OR demolding_date = ""').fetchone()[0]
print(f'Registros con fecha NULL o vacía: {cnt}')

# Get samples
rows = cur.execute('SELECT material, flask_id, cancha, demolding_date, mold_quantity FROM sap_demolding_snapshot WHERE demolding_date IS NULL OR demolding_date = "" LIMIT 10').fetchall()
print('\nPrimeros 10 registros con fecha inválida:')
for r in rows:
    print(f"  Material: {r[0]}, Flask: {r[1]}, Cancha: {r[2]}, Fecha: {r[3]}, Qty: {r[4]}")

conn.close()
