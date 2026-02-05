"""Debug script to check planner_daily_resources table."""
from datetime import date
import sqlite3

db_path = "db/foundryplan.db"

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

today = date.today()
today_str = today.isoformat()

print(f"=== Diagnóstico de planner_daily_resources ===")
print(f"Fecha de hoy: {today_str} ({today.strftime('%A, %d de %B de %Y')})")
print()

# Check if table exists
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='planner_daily_resources'")
if not cur.fetchone():
    print("❌ Tabla planner_daily_resources NO EXISTE")
    exit(1)

print("✅ Tabla planner_daily_resources existe")
print()

# Count total rows
cur.execute("SELECT COUNT(*) FROM planner_daily_resources WHERE scenario_id = 1")
total_rows = cur.fetchone()[0]
print(f"Total de registros en scenario_id=1: {total_rows}")
print()

# Check for today's data
cur.execute("""
    SELECT day, flask_type, available_qty, 
           molding_capacity_per_day, same_mold_capacity_per_day, pouring_tons_available
    FROM planner_daily_resources
    WHERE scenario_id = 1 AND day = ?
    ORDER BY flask_type
""", (today_str,))

today_rows = cur.fetchall()

if today_rows:
    print(f"✅ Datos para HOY ({today_str}):")
    for row in today_rows:
        print(f"  - Flask {row['flask_type']}: {row['available_qty']} disponibles")
        print(f"    Capacidad moldeo: {row['molding_capacity_per_day']}, mismo molde: {row['same_mold_capacity_per_day']}")
        print(f"    Fusión: {row['pouring_tons_available']} tons")
else:
    print(f"❌ NO HAY datos para hoy ({today_str})")
    print()
    print("Buscando días cercanos...")
    cur.execute("""
        SELECT DISTINCT day 
        FROM planner_daily_resources 
        WHERE scenario_id = 1 
        ORDER BY day 
        LIMIT 10
    """)
    nearby_days = cur.fetchall()
    if nearby_days:
        print("Primeros 10 días en la tabla:")
        for row in nearby_days:
            print(f"  - {row['day']}")

print()

# Check flask configuration
cur.execute("""
    SELECT flask_type, qty_total, codes_csv 
    FROM planner_flask_types 
    WHERE scenario_id = 1
    ORDER BY flask_type
""")

flask_config = cur.fetchall()
if flask_config:
    print("Configuración de flasks:")
    for row in flask_config:
        print(f"  - {row['flask_type']}: {row['qty_total']} totales (códigos: {row['codes_csv']})")
else:
    print("❌ Sin configuración de flasks")

print()

# Check demolding data
cur.execute("SELECT COUNT(*) FROM sap_demolding_snapshot")
demolding_count = cur.fetchone()[0]
print(f"Registros en sap_demolding_snapshot: {demolding_count}")

if demolding_count > 0:
    cur.execute("""
        SELECT cancha, COUNT(*) as cnt 
        FROM sap_demolding_snapshot 
        GROUP BY cancha
    """)
    print("Distribución por cancha:")
    for row in cur.fetchall():
        print(f"  - {row['cancha'] or '(null)'}: {row['cnt']} registros")

conn.close()
