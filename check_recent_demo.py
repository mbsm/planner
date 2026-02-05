"""Check recent demolding data."""
import sqlite3
from datetime import date

conn = sqlite3.connect('db/foundryplan.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

rows = cur.execute('''
    SELECT cancha, demolding_date, COUNT(*) as cnt, SUM(CAST(mold_quantity AS INTEGER)) as total_qty 
    FROM sap_demolding_snapshot 
    WHERE demolding_date >= "2026-02-01" 
    GROUP BY cancha, demolding_date 
    ORDER BY demolding_date DESC, cancha
''').fetchall()

print('Desmoldeo reciente (desde Feb 2026):')
if rows:
    for r in rows[:20]:
        print(f"  {r['demolding_date']} - {r['cancha']}: {r['cnt']} registros, {r['total_qty']} moldes totales")
else:
    print("  Sin datos recientes")

print()
print("Verificando cajas ocupadas HOY en TODAS las canchas:")
today = date.today().isoformat()

rows2 = cur.execute('''
    SELECT cancha, 
           SUBSTR(flask_id, 1, 3) as flask_type,
           SUM(CAST(mold_quantity AS INTEGER)) as qty_occupied
    FROM sap_demolding_snapshot
    WHERE demolding_date >= ?
    GROUP BY cancha, SUBSTR(flask_id, 1, 3)
    ORDER BY cancha, flask_type
''', (today,)).fetchall()

if rows2:
    for r in rows2:
        if r['qty_occupied'] and r['qty_occupied'] > 0:
            print(f"  Cancha {r['cancha']}: Flask {r['flask_type']} - {r['qty_occupied']} ocupados")
else:
    print("  Sin cajas ocupadas")

conn.close()
