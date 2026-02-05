"""Debug script to check demolding snapshot."""
from datetime import date
import sqlite3

db_path = "db/foundryplan.db"

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

today = date.today()
today_str = today.isoformat()

print(f"=== Diagnóstico de Desmoldeo ===")
print(f"Fecha de hoy: {today_str}")
print()

# Get planner config
cur.execute("SELECT config_value FROM app_config WHERE config_key = 'planner_demolding_cancha'")
row = cur.fetchone()
cancha_filter = row['config_value'] if row else 'TCF-L1400'
print(f"Cancha configurada: {cancha_filter}")
print()

# Get demolding data for configured cancha
cur.execute("""
    SELECT material, lote, flask_id, demolding_date, mold_quantity, cooling_hours
    FROM sap_demolding_snapshot
    WHERE cancha = ?
    ORDER BY demolding_date
""", (cancha_filter,))

rows = cur.fetchall()
print(f"Registros de desmoldeo para cancha '{cancha_filter}': {len(rows)}")
print()

if rows:
    print("Primeros 10 registros:")
    for i, row in enumerate(rows[:10]):
        flask_code = row['flask_id'][:3] if row['flask_id'] and len(row['flask_id']) >= 3 else row['flask_id']
        demo_date = row['demolding_date']
        still_occupied = demo_date >= today_str if demo_date else False
        status = "🔴 OCUPADO" if still_occupied else "✅ LIBERADO"
        print(f"{i+1}. Material: {row['material']}, Flask: {row['flask_id']} (tipo: {flask_code})")
        print(f"   Desmoldeo: {demo_date}, Qty: {row['mold_quantity']}, Enfriamiento: {row['cooling_hours']}h {status}")
        print()
    
    # Count by flask type
    print("Conteo por tipo de flask (primeros 3 caracteres):")
    flask_counts = {}
    for row in rows:
        flask_code = row['flask_id'][:3] if row['flask_id'] and len(row['flask_id']) >= 3 else row['flask_id']
        demo_date = row['demolding_date']
        still_occupied = demo_date >= today_str if demo_date else False
        
        if still_occupied:
            qty = int(row['mold_quantity'] or 1)
            flask_counts[flask_code] = flask_counts.get(flask_code, 0) + qty
    
    print("\nFlasks OCUPADOS (demolding_date >= hoy):")
    for flask_type, count in sorted(flask_counts.items()):
        print(f"  - Flask {flask_type}: {count} ocupados")

else:
    print(f"⚠️ No hay datos de desmoldeo para cancha '{cancha_filter}'")

conn.close()
