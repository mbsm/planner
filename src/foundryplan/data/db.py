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

                CREATE TABLE IF NOT EXISTS parts (
                    numero_parte TEXT PRIMARY KEY,
                    familia TEXT NOT NULL
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
                    x_programar INTEGER,
                    programado INTEGER,
                    por_fundir INTEGER,
                    desmoldeo INTEGER,
                    tt INTEGER,
                    terminaciones INTEGER,
                    mecanizado_interno INTEGER,
                    mecanizado_externo INTEGER,
                    vulcanizado INTEGER,
                    insp_externa INTEGER,
                    cliente TEXT,
                    oc_cliente TEXT,
                    peso_neto REAL,
                    peso_unitario_ton REAL,
                    bodega INTEGER,
                    despachado INTEGER,
                    rechazo INTEGER
                );

                CREATE TABLE IF NOT EXISTS vision_kpi_daily (
                    snapshot_date TEXT PRIMARY KEY,
                    snapshot_at TEXT NOT NULL,
                    tons_por_entregar REAL NOT NULL,
                    tons_atrasadas REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mb52_progress_last (
                    process TEXT PRIMARY KEY,
                    generated_on TEXT NOT NULL,
                    report_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vision_progress_last (
                    id INTEGER PRIMARY KEY,
                    generated_on TEXT NOT NULL,
                    report_json TEXT NOT NULL
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

            # sap_vision v2: add optional weight fields (tons)
            vision_cols = [r[1] for r in con.execute("PRAGMA table_info(sap_vision)").fetchall()]
            if "peso_neto" not in vision_cols:
                try:
                    con.execute("ALTER TABLE sap_vision ADD COLUMN peso_neto REAL")
                except Exception:
                    pass
            if "peso_unitario_ton" not in vision_cols:
                try:
                    con.execute("ALTER TABLE sap_vision ADD COLUMN peso_unitario_ton REAL")
                except Exception:
                    pass

            # sap_vision v3: add progress fields (bodega / despachado)
            vision_cols = [r[1] for r in con.execute("PRAGMA table_info(sap_vision)").fetchall()]
            if "bodega" not in vision_cols:
                try:
                    con.execute("ALTER TABLE sap_vision ADD COLUMN bodega INTEGER")
                except Exception:
                    pass
            if "despachado" not in vision_cols:
                try:
                    con.execute("ALTER TABLE sap_vision ADD COLUMN despachado INTEGER")
                except Exception:
                    pass

            # sap_vision v4: optional per-stage piece counts
            vision_cols = [r[1] for r in con.execute("PRAGMA table_info(sap_vision)").fetchall()]
            for col in (
                "x_programar",
                "programado",
                "por_fundir",
                "desmoldeo",
                "tt",
                "terminaciones",
                "mecanizado_interno",
                "mecanizado_externo",
                "vulcanizado",
                "insp_externa",
                "rechazo",
                "en_vulcanizado",
                "pend_vulcanizado",
                "rech_insp_externa",
                "lib_vulcanizado_de",
            ):
                if col in vision_cols:
                    continue
                try:
                    con.execute(f"ALTER TABLE sap_vision ADD COLUMN {col} INTEGER")
                except Exception:
                    pass

            # sap_vision v5: add tipo_posicion field
            if "tipo_posicion" not in vision_cols:
                try:
                    con.execute("ALTER TABLE sap_vision ADD COLUMN tipo_posicion TEXT")
                except Exception:
                    pass

            # sap_vision v6: add status_comercial field
            vision_cols = [r[1] for r in con.execute("PRAGMA table_info(sap_vision)").fetchall()]
            if "status_comercial" not in vision_cols:
                try:
                    con.execute("ALTER TABLE sap_vision ADD COLUMN status_comercial TEXT")
                except Exception:
                    pass

            # parts table v2: add optional post-process lead times (days)
            part_cols = [r[1] for r in con.execute("PRAGMA table_info(parts)").fetchall()]
            if "vulcanizado_dias" not in part_cols:
                con.execute("ALTER TABLE parts ADD COLUMN vulcanizado_dias INTEGER")
            if "mecanizado_dias" not in part_cols:
                con.execute("ALTER TABLE parts ADD COLUMN mecanizado_dias INTEGER")
            if "inspeccion_externa_dias" not in part_cols:
                con.execute("ALTER TABLE parts ADD COLUMN inspeccion_externa_dias INTEGER")
            # parts table v3: optional weight per piece (tons)
            if "peso_ton" not in part_cols:
                try:
                    con.execute("ALTER TABLE parts ADD COLUMN peso_ton REAL")
                except Exception:
                    pass

            # parts table v4: binary master attributes
            if "mec_perf_inclinada" not in part_cols:
                try:
                    con.execute(
                        "ALTER TABLE parts ADD COLUMN mec_perf_inclinada INTEGER NOT NULL DEFAULT 0"
                    )
                except Exception:
                    pass
            if "sobre_medida" not in part_cols:
                try:
                    con.execute(
                        "ALTER TABLE parts ADD COLUMN sobre_medida INTEGER NOT NULL DEFAULT 0"
                    )
                except Exception:
                    pass

            # Seed default catalog entries only if catalog is empty.
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
            con.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_centro', '4000')")
            con.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_almacen_terminaciones', '4035')")
            con.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_material_prefixes', '436')")

            # New process: Toma de dureza. Defaults to Terminaciones warehouse.
            con.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_almacen_toma_dureza', '4035')")

            # Other process warehouses (can be edited in UI).
            con.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_almacen_mecanizado', '4049')")
            con.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_almacen_mecanizado_externo', '4050')")
            con.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_almacen_inspeccion_externa', '4046')")
            con.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_almacen_por_vulcanizar', '4047')")
            con.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES('sap_almacen_en_vulcanizado', '4048')")

            # ----- Per-process tables (best-effort migrations) -----
            # line_config: v2 adds `process` and composite primary key.
            row = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='line_config'"
            ).fetchone()
            if int(row[0]) == 0:
                con.execute(
                    "CREATE TABLE line_config(process TEXT NOT NULL, line_id INTEGER NOT NULL, line_name TEXT, families_json TEXT NOT NULL, PRIMARY KEY(process, line_id))"
                )
            else:
                cols = [r[1] for r in con.execute("PRAGMA table_info(line_config)").fetchall()]
                if "process" not in cols:
                    try:
                        con.execute("ALTER TABLE line_config RENAME TO line_config_old")
                        con.execute(
                            "CREATE TABLE line_config(process TEXT NOT NULL, line_id INTEGER NOT NULL, line_name TEXT, families_json TEXT NOT NULL, PRIMARY KEY(process, line_id))"
                        )
                        con.execute(
                            "INSERT OR IGNORE INTO line_config(process, line_id, line_name, families_json) SELECT 'terminaciones', line_id, NULL, families_json FROM line_config_old"
                        )
                    except Exception:
                        pass

                # line_config v3: add optional line_name
                cols = [r[1] for r in con.execute("PRAGMA table_info(line_config)").fetchall()]
                if "line_name" not in cols:
                    try:
                        con.execute("ALTER TABLE line_config ADD COLUMN line_name TEXT")
                    except Exception:
                        pass

            # last_program: v2 uses process as primary key.
            row = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='last_program'"
            ).fetchone()
            if int(row[0]) == 0:
                con.execute(
                    "CREATE TABLE last_program(process TEXT PRIMARY KEY, generated_on TEXT NOT NULL, program_json TEXT NOT NULL)"
                )
            else:
                cols = [r[1] for r in con.execute("PRAGMA table_info(last_program)").fetchall()]
                if "process" not in cols:
                    try:
                        con.execute("ALTER TABLE last_program RENAME TO last_program_old")
                        con.execute(
                            "CREATE TABLE last_program(process TEXT PRIMARY KEY, generated_on TEXT NOT NULL, program_json TEXT NOT NULL)"
                        )
                        con.execute(
                            "INSERT OR IGNORE INTO last_program(process, generated_on, program_json) "
                            "SELECT 'terminaciones', generated_on, program_json FROM last_program_old WHERE id = 1"
                        )
                    except Exception:
                        pass

            # program_in_progress: locks to keep selected order positions pinned per line.
            # Keyed by (process, pedido, posicion, is_test).
            try:
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS program_in_progress (
                        process TEXT NOT NULL,
                        pedido TEXT NOT NULL,
                        posicion TEXT NOT NULL,
                        is_test INTEGER NOT NULL DEFAULT 0,
                        line_id INTEGER NOT NULL,
                        marked_at TEXT NOT NULL,
                        PRIMARY KEY (process, pedido, posicion, is_test)
                    );
                    """.strip()
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_program_in_progress_lookup ON program_in_progress(process, line_id, marked_at)"
                )
            except Exception:
                # Best-effort migrations should not prevent startup.
                pass

            # program_in_progress_item: split-aware locks.
            # Multiple rows (split_id) can exist per (process,pedido,posicion,is_test).
            # qty=0 means "auto" (use full remaining quantity when merging).
            try:
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS program_in_progress_item (
                        process TEXT NOT NULL,
                        pedido TEXT NOT NULL,
                        posicion TEXT NOT NULL,
                        is_test INTEGER NOT NULL DEFAULT 0,
                        split_id INTEGER NOT NULL,
                        line_id INTEGER NOT NULL,
                        qty INTEGER NOT NULL DEFAULT 0,
                        marked_at TEXT NOT NULL,
                        PRIMARY KEY (process, pedido, posicion, is_test, split_id)
                    );
                    """.strip()
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_program_in_progress_item_lookup ON program_in_progress_item(process, line_id, marked_at)"
                )

                # Best-effort migration from legacy single-lock table.
                con.execute(
                    """
                    INSERT OR IGNORE INTO program_in_progress_item(process, pedido, posicion, is_test, split_id, line_id, qty, marked_at)
                    SELECT process, pedido, posicion, is_test, 1 AS split_id, line_id, 0 AS qty, marked_at
                    FROM program_in_progress
                    """.strip()
                )
            except Exception:
                # Best-effort migrations should not prevent startup.
                pass

            # orders: v4 adds process+almacen.
            row = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='orders'"
            ).fetchone()
            if int(row[0]) == 0:
                con.execute(
                    """
                    CREATE TABLE orders (
                        process TEXT NOT NULL,
                        almacen TEXT NOT NULL,
                        pedido TEXT NOT NULL,
                        posicion TEXT NOT NULL,
                        numero_parte TEXT NOT NULL,
                        cantidad INTEGER NOT NULL,
                        fecha_entrega TEXT NOT NULL,
                        primer_correlativo INTEGER NOT NULL,
                        ultimo_correlativo INTEGER NOT NULL,
                        tiempo_proceso_min REAL,
                        is_test INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (process, pedido, posicion, primer_correlativo, ultimo_correlativo)
                    );
                    """
                )
            else:
                cols = [r[1] for r in con.execute("PRAGMA table_info(orders)").fetchall()]
                if "process" not in cols:
                    try:
                        def table_exists(name: str) -> bool:
                            return (
                                con.execute(
                                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                    (name,),
                                ).fetchone()
                                is not None
                            )

                        def rename_table(src: str, dst: str) -> None:
                            con.execute(f'ALTER TABLE "{src}" RENAME TO "{dst}"')

                        # If a previous best-effort migration already created orders_old,
                        # move it aside so we can rename the current orders table.
                        if table_exists("orders_old"):
                            suffix = 1
                            while table_exists(f"orders_old_{suffix}"):
                                suffix += 1
                            rename_table("orders_old", f"orders_old_{suffix}")

                        rename_table("orders", "orders_old")

                        con.execute(
                            """
                            CREATE TABLE orders (
                                process TEXT NOT NULL,
                                almacen TEXT NOT NULL,
                                pedido TEXT NOT NULL,
                                posicion TEXT NOT NULL,
                                numero_parte TEXT NOT NULL,
                                cantidad INTEGER NOT NULL,
                                fecha_entrega TEXT NOT NULL,
                                primer_correlativo INTEGER NOT NULL,
                                ultimo_correlativo INTEGER NOT NULL,
                                tiempo_proceso_min REAL,
                                is_test INTEGER NOT NULL DEFAULT 0,
                                PRIMARY KEY (process, pedido, posicion, primer_correlativo, ultimo_correlativo)
                            );
                            """
                        )

                        almacen_term_row = con.execute(
                            "SELECT value FROM app_config WHERE key='sap_almacen_terminaciones'"
                        ).fetchone()
                        almacen_term = str((almacen_term_row[0] if almacen_term_row else "4035") or "4035")

                        old_cols = [r[1] for r in con.execute("PRAGMA table_info(orders_old)").fetchall()]

                        has_posicion = "posicion" in old_cols
                        has_is_test = "is_test" in old_cols

                        if has_posicion:
                            is_test_expr = "COALESCE(is_test, 0)" if has_is_test else "0"
                            con.execute(
                                "INSERT OR IGNORE INTO orders(process, almacen, pedido, posicion, numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test) "
                                f"SELECT 'terminaciones', ?, pedido, posicion, numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, {is_test_expr} FROM orders_old",
                                (almacen_term,),
                            )
                        else:
                            # Legacy (no posicion): keep placeholder.
                            con.execute(
                                "INSERT OR IGNORE INTO orders(process, almacen, pedido, posicion, numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test) "
                                "SELECT 'terminaciones', ?, pedido, '0000', numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, 0 FROM orders_old",
                                (almacen_term,),
                            )
                    except Exception:
                        # Best-effort migrations should not prevent startup.
                        pass

            # ----- Best-effort normalization of SAP key columns -----
            # Excel often turns keys like 4049 into 4049.0; normalize trailing ".0" and trim.
            # This helps per-process warehouse filtering and MB52<->Visión joins.
            for table, cols in (
                ("sap_mb52", ["centro", "almacen", "documento_comercial", "posicion_sd"]),
                ("sap_vision", ["pedido", "posicion"]),
            ):
                try:
                    existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
                    for col in cols:
                        if col not in existing:
                            continue
                        con.execute(
                            f"UPDATE {table} SET {col} = TRIM({col}) WHERE {col} IS NOT NULL AND TRIM({col}) <> ''"
                        )
                        con.execute(
                            f"UPDATE {table} SET {col} = SUBSTR({col}, 1, LENGTH({col}) - 2) "
                            f"WHERE {col} IS NOT NULL AND TRIM({col}) LIKE '%.0'"
                        )
                except Exception:
                    pass
