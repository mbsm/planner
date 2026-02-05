"""Check demolding between Feb 4-6."""
import sqlite3

conn = sqlite3.connect('db/foundryplan.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

rows = cur.execute('''
    SELECT material, flask_id, cancha, demolding_date, mold_quantity
    FROM sap_demolding_snapshot
    WHERE cancha LIKE 'TCF%'
      AND demolding_date BETWEEN '2026-02-04' AND '2026-02-06'
    ORDER BY demolding_date, cancha, flask_id
''').fetchall()

print(f'Desmoldeos programados entre 4-6 Feb en canchas TCF: {len(rows)} registros')
print()

for r in rows:
    flask_code = r['flask_id'][:3] if r['flask_id'] else '???'
    print(f"{r['demolding_date']} - {r['cancha']} - Flask {flask_code} ({r['flask_id']}): {r['mold_quantity']} moldes")
    print(f"  Material: {r['material']}")

conn.close()
