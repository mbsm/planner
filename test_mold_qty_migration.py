"""Test script to verify mold_quantity conversion to REAL."""
import sys
sys.path.insert(0, 'src')

from pathlib import Path
from foundryplan.data.db import Db
import sqlite3

# Initialize DB (will run migrations)
db = Db(path=Path("db/foundryplan.db"))

with db.connect() as conn:
    # Check schema
    cur = conn.execute("PRAGMA table_info(sap_demolding_snapshot)")
    columns = cur.fetchall()

    print("=== Schema de sap_demolding_snapshot ===")
    for col in columns:
        cid, name, dtype, notnull, dflt_value, pk = col
        print(f"{name:20s} {dtype:10s} {'NOT NULL' if notnull else ''} {'PK' if pk else ''}")

    print("\n=== Verificando datos actuales ===")
    cur = conn.execute("""
        SELECT material, flask_id, mold_quantity, typeof(mold_quantity) as type
        FROM sap_demolding_snapshot 
        WHERE cancha = 'TCF-L1400'
        LIMIT 10
    """)

    rows = cur.fetchall()
    print(f"Registros en TCF-L1400: {len(rows)}")
    for r in rows:
        print(f"  Material: {r[0]}, Flask: {r[1]}, Qty: {r[2]} (tipo: {r[3]})")

    # Check if we have any non-zero decimal values
    cur = conn.execute("""
        SELECT COUNT(*) as cnt, 
               MIN(mold_quantity) as min_qty,
               MAX(mold_quantity) as max_qty,
               AVG(mold_quantity) as avg_qty
        FROM sap_demolding_snapshot
        WHERE mold_quantity > 0
    """)
    stats = cur.fetchone()
    print(f"\nEstadísticas de mold_quantity (valores > 0):")
    print(f"  Count: {stats[0]}")
    print(f"  Min: {stats[1]}")
    print(f"  Max: {stats[2]}")
    print(f"  Avg: {stats[3]}")


