"""Check daily resources for first week."""
import sqlite3

conn = sqlite3.connect('db/foundryplan.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Check first week (Feb 2-8)
rows = cur.execute('''
    SELECT day, flask_type, available_qty
    FROM planner_daily_resources
    WHERE scenario_id = 1
      AND day BETWEEN '2026-02-02' AND '2026-02-08'
    ORDER BY day, flask_type
''').fetchall()

print('Disponibilidad primera semana (02-Feb / 08-Feb):')
print()

# Group by day
from collections import defaultdict
by_day = defaultdict(list)
for r in rows:
    by_day[r['day']].append(r)

for day in sorted(by_day.keys()):
    print(f"{day}:")
    for r in by_day[day]:
        print(f"  Flask {r['flask_type']}: {r['available_qty']} disponibles")
    print()

# Calculate minimums
print("Mínimos de la semana por flask_type:")
minimums = {}
for r in rows:
    flask = r['flask_type']
    qty = r['available_qty']
    if flask not in minimums or qty < minimums[flask]:
        minimums[flask] = qty

for flask in sorted(minimums.keys()):
    print(f"  Flask {flask}: {minimums[flask]} (mínimo)")

conn.close()
