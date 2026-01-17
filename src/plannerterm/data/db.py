from __future__ import annotations

import sqlite3
from pathlib import Path


class Db:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def ensure_schema(self) -> None:
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL;")

            # Required for ON DELETE/UPDATE behaviors if we add FKs later.
            con.execute("PRAGMA foreign_keys=ON;")

            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS families (
                    name TEXT PRIMARY KEY
                );

                CREATE TABLE IF NOT EXISTS app_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS line_config (
                    line_id INTEGER PRIMARY KEY,
                    families_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS parts (
                    numero_parte TEXT PRIMARY KEY,
                    familia TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS last_program (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    generated_on TEXT NOT NULL,
                    program_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sap_mb52 (
                    material TEXT NOT NULL,
                    texto_breve TEXT,
                    centro TEXT,
                    almacen TEXT,
                    lote TEXT,
                    libre_utilizacion INTEGER,
                    documento_comercial TEXT,
                    posicion_sd TEXT,
                    en_control_calidad INTEGER
                );

                CREATE TABLE IF NOT EXISTS sap_vision (
                    pedido TEXT NOT NULL,
                    posicion TEXT NOT NULL,
                    cod_material TEXT,
                    descripcion_material TEXT,
                    fecha_pedido TEXT NOT NULL,
                    fecha_entrega TEXT,
                    solicitado INTEGER,
                    cliente TEXT,
                    oc_cliente TEXT
                );

                CREATE TABLE IF NOT EXISTS order_priority (
                    pedido TEXT PRIMARY KEY,
                    is_priority INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS orderpos_priority (
                    pedido TEXT NOT NULL,
                    posicion TEXT NOT NULL,
                    is_priority INTEGER NOT NULL DEFAULT 0,
                    kind TEXT,
                    PRIMARY KEY (pedido, posicion)
                );
                """
            )

            # orderpos_priority v2: add kind (manual/test)
            opp_cols = [r[1] for r in con.execute("PRAGMA table_info(orderpos_priority)").fetchall()]
            if "kind" not in opp_cols:
                con.execute("ALTER TABLE orderpos_priority ADD COLUMN kind TEXT")

            # parts table v2: add optional post-process lead times (days)
            part_cols = [r[1] for r in con.execute("PRAGMA table_info(parts)").fetchall()]
            if "vulcanizado_dias" not in part_cols:
                con.execute("ALTER TABLE parts ADD COLUMN vulcanizado_dias INTEGER")
            if "mecanizado_dias" not in part_cols:
                con.execute("ALTER TABLE parts ADD COLUMN mecanizado_dias INTEGER")
            if "inspeccion_externa_dias" not in part_cols:
                con.execute("ALTER TABLE parts ADD COLUMN inspeccion_externa_dias INTEGER")

            # Seed default catalog entries only if catalog is empty.
            # This allows users to delete/rename defaults and keep the result persistent.
            families_count = int(con.execute("SELECT COUNT(*) FROM families").fetchone()[0])
            if families_count == 0:
                con.executemany(
                    "INSERT OR IGNORE INTO families(name) VALUES(?)",
                    [("Parrillas",), ("Lifters",), ("Corazas",), ("Otros",), ("No pieza",)],
                )

            # Ensure special catalog entries exist even on existing databases.
            con.execute("INSERT OR IGNORE INTO families(name) VALUES(?)", ("No pieza",))

            # Migrate any existing familias from parts into the catalog.
            con.execute(
                "INSERT OR IGNORE INTO families(name) SELECT DISTINCT familia FROM parts WHERE familia IS NOT NULL AND TRIM(familia) <> ''"
            )

            # Seed default SAP config values if missing.
            con.execute(
                "INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_centro', '4000')"
            )
            con.execute(
                "INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_almacen_terminaciones', '4035')"
            )

            # orders table v3 migration (pedido can repeat; add posicion and composite key)
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    pedido TEXT NOT NULL,
                    posicion TEXT NOT NULL,
                    numero_parte TEXT NOT NULL,
                    cantidad INTEGER NOT NULL,
                    fecha_entrega TEXT NOT NULL,
                    primer_correlativo INTEGER NOT NULL,
                    ultimo_correlativo INTEGER NOT NULL,
                    tiempo_proceso_min REAL,
                    is_test INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (pedido, posicion, primer_correlativo, ultimo_correlativo)
                );
                """
            )

            cols = [r[1] for r in con.execute("PRAGMA table_info(orders)").fetchall()]
            expected = {
                "pedido",
                "posicion",
                "numero_parte",
                "cantidad",
                "fecha_entrega",
                "primer_correlativo",
                "ultimo_correlativo",
                "tiempo_proceso_min",
                "is_test",
            }

            if set(cols) != expected:
                # Best-effort migration from older schema.
                # If coming from v2 (pedido PK), keep rows as orders_old for manual recovery.
                con.executescript(
                    """
                    ALTER TABLE orders RENAME TO orders_old;
                    CREATE TABLE orders (
                        pedido TEXT NOT NULL,
                        posicion TEXT NOT NULL,
                        numero_parte TEXT NOT NULL,
                        cantidad INTEGER NOT NULL,
                        fecha_entrega TEXT NOT NULL,
                        primer_correlativo INTEGER NOT NULL,
                        ultimo_correlativo INTEGER NOT NULL,
                        tiempo_proceso_min REAL,
                        is_test INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (pedido, posicion, primer_correlativo, ultimo_correlativo)
                    );
                    """
                )
