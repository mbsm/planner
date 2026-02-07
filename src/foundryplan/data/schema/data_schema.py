from __future__ import annotations

import sqlite3


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS core_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT(datetime('now', 'localtime')),
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            details TEXT
        );

        CREATE TABLE IF NOT EXISTS core_family_catalog (
            family_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS core_config (
            config_key TEXT PRIMARY KEY,
            config_value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS core_alloy_catalog (
            alloy_code TEXT PRIMARY KEY,
            alloy_name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS core_material_master (
            part_code TEXT PRIMARY KEY,
            descripcion_pieza TEXT,
            family_id TEXT,
            aleacion TEXT,
            flask_size TEXT,
            piezas_por_molde REAL,
            tiempo_enfriamiento_molde_horas INTEGER,
            finish_days INTEGER,
            min_finish_days INTEGER,
            vulcanizado_dias INTEGER,
            mecanizado_dias INTEGER,
            inspeccion_externa_dias INTEGER,
            peso_unitario_ton REAL,
            mec_perf_inclinada INTEGER NOT NULL DEFAULT 0,
            sobre_medida_mecanizado INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(family_id) REFERENCES core_family_catalog(family_id)
        );

        CREATE TABLE IF NOT EXISTS core_processes (
            process_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            sap_almacen TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            is_special_moldeo INTEGER NOT NULL DEFAULT 0,
            availability_predicate_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE VIEW IF NOT EXISTS process AS
            SELECT process_id, label, sap_almacen, is_active, is_special_moldeo, availability_predicate_json, created_at
            FROM core_processes;

        CREATE TABLE IF NOT EXISTS resource (
            resource_id TEXT PRIMARY KEY,
            process_id TEXT NOT NULL,
            name TEXT NOT NULL,
            capacity_per_day REAL,
            sort_order INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(process_id) REFERENCES core_processes(process_id)
        );

        CREATE TABLE IF NOT EXISTS resource_constraint (
            resource_id TEXT NOT NULL,
            attr_key TEXT NOT NULL,
            rule_type TEXT,
            rule_value_json TEXT,
            PRIMARY KEY(resource_id, attr_key),
            FOREIGN KEY(resource_id) REFERENCES resource(resource_id)
        );

        CREATE TABLE IF NOT EXISTS process_attribute_def (
            process_id TEXT NOT NULL,
            attr_key TEXT NOT NULL,
            attr_type TEXT,
            allowed_values_json TEXT,
            min_value REAL,
            max_value REAL,
            is_required INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(process_id, attr_key),
            FOREIGN KEY(process_id) REFERENCES core_processes(process_id)
        );

        CREATE TABLE IF NOT EXISTS core_sap_mb52_snapshot (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            loaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            material TEXT NOT NULL,
            material_base TEXT,
            texto_breve TEXT,
            centro TEXT,
            almacen TEXT,
            lote TEXT,
            pb_almacen REAL,
            libre_utilizacion INTEGER,
            documento_comercial TEXT,
            posicion_sd TEXT,
            en_control_calidad INTEGER,
            correlativo_int INTEGER,
            is_test INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS core_sap_vision_snapshot (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            loaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            pedido TEXT NOT NULL,
            posicion TEXT NOT NULL,
            tipo_posicion TEXT,
            tipo_de_reparto TEXT,
            cliente TEXT,
            n_oc_cliente TEXT,
            pos_oc TEXT,
            material_client_code TEXT,
            cod_material TEXT,
            descripcion_material TEXT,
            atributo TEXT,
            fecha_de_pedido TEXT NOT NULL,
            solicitado INTEGER,
            x_programar INTEGER,
            programado INTEGER,
            x_fundir INTEGER,
            desmoldeo INTEGER,
            tt INTEGER,
            terminacion INTEGER,
            mecanizado_interno INTEGER,
            mecanizado_externo INTEGER,
            vulcanizado INTEGER,
            en_vulcaniz INTEGER,
            pend_vulcanizado INTEGER,
            insp_externa INTEGER,
            rech_insp_externa INTEGER,
            lib_vulcaniz_de INTEGER,
            bodega INTEGER,
            despachado INTEGER,
            rechazo INTEGER,
            ret_qm INTEGER,
            grupo_art TEXT,
            proveedor TEXT,
            status TEXT,
            status_comercial TEXT,
            jerarquia_productos TEXT,
            peso_neto_ton REAL,
            peso_unitario_ton REAL
        );

        CREATE VIEW IF NOT EXISTS core_sap_vision AS SELECT * FROM core_sap_vision_snapshot;

        -- DEPRECATED: Replaced by core_moldes_por_fundir and core_piezas_fundidas
        CREATE TABLE IF NOT EXISTS core_sap_demolding_snapshot (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            loaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            material TEXT,
            lote TEXT,
            flask_id TEXT,
            cancha TEXT,
            demolding_date TEXT,
            demolding_time TEXT,
            mold_type TEXT,
            poured_date TEXT,
            poured_time TEXT,
            cooling_hours REAL,
            mold_quantity REAL
        );

        -- Moldes en proceso de fundición (sin fecha_desmoldeo)
        CREATE TABLE IF NOT EXISTS core_moldes_por_fundir (
            molde_id INTEGER PRIMARY KEY AUTOINCREMENT,
            loaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            material TEXT NOT NULL,
            tipo_pieza TEXT NOT NULL,
            lote TEXT,
            flask_id TEXT NOT NULL,
            cancha TEXT NOT NULL,
            mold_type TEXT,
            poured_date TEXT,
            poured_time TEXT,
            cooling_hours REAL,
            mold_quantity REAL
        );

        -- Piezas ya fundidas (con fecha_desmoldeo)
        CREATE TABLE IF NOT EXISTS core_piezas_fundidas (
            pieza_id INTEGER PRIMARY KEY AUTOINCREMENT,
            loaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            material TEXT NOT NULL,
            tipo_pieza TEXT NOT NULL,
            lote TEXT,
            flask_id TEXT NOT NULL,
            cancha TEXT NOT NULL,
            demolding_date TEXT NOT NULL,
            demolding_time TEXT,
            mold_type TEXT,
            poured_date TEXT,
            poured_time TEXT,
            cooling_hours REAL,
            mold_quantity REAL
        );

        CREATE TABLE IF NOT EXISTS core_orders (
            process TEXT NOT NULL,
            almacen TEXT NOT NULL,
            pedido TEXT NOT NULL,
            posicion TEXT NOT NULL,
            material TEXT NOT NULL,
            cantidad INTEGER NOT NULL,
            fecha_de_pedido TEXT NOT NULL,
            primer_correlativo INTEGER NOT NULL,
            ultimo_correlativo INTEGER NOT NULL,
            tiempo_proceso_min REAL,
            is_test INTEGER NOT NULL DEFAULT 0,
            cliente TEXT,
            PRIMARY KEY (process, pedido, posicion, primer_correlativo, ultimo_correlativo)
        );

        CREATE TABLE IF NOT EXISTS core_vision_kpi_daily (
            snapshot_date TEXT PRIMARY KEY,
            snapshot_at TEXT NOT NULL,
            tons_por_entregar REAL NOT NULL,
            tons_atrasadas REAL NOT NULL
        );
        """
    )

    con.executemany(
        "INSERT OR IGNORE INTO core_family_catalog(family_id, label) VALUES(?, ?)",
        [
            ("Parrillas", "Parrillas"),
            ("Lifters", "Lifters"),
            ("Corazas", "Corazas"),
            ("Otros", "Otros"),
            ("No pieza", "No pieza"),
        ],
    )

    con.execute("INSERT OR IGNORE INTO core_config(config_key, config_value) VALUES('sap_centro', '4000')")
    con.execute("INSERT OR IGNORE INTO core_config(config_key, config_value) VALUES('sap_center', '4000')")
    con.execute("INSERT OR IGNORE INTO core_config(config_key, config_value) VALUES('sap_material_prefixes', '436')")
    con.execute("INSERT OR IGNORE INTO core_config(config_key, config_value) VALUES('job_priority_map', '{\"prueba\": 1, \"urgente\": 2, \"normal\": 3}')")
    con.execute("INSERT OR IGNORE INTO core_config(config_key, config_value) VALUES('planner_horizon_days', '30')")
    con.execute("INSERT OR IGNORE INTO core_config(config_key, config_value) VALUES('planner_horizon_buffer_days', '10')")
    con.execute("INSERT OR IGNORE INTO core_config(config_key, config_value) VALUES('planner_holidays', '')")

    con.executemany(
        "INSERT OR IGNORE INTO core_config(config_key, config_value) VALUES(?, ?)",
        [
            ("sap_almacen_moldeo", "4032"),
            ("sap_almacen_terminaciones", "4035"),
            ("sap_almacen_toma_dureza", "4035"),
            ("sap_almacen_mecanizado", "4049"),
            ("sap_almacen_mecanizado_externo", "4050"),
            ("sap_almacen_inspeccion_externa", "4046"),
            ("sap_almacen_por_vulcanizar", "4047"),
            ("sap_almacen_en_vulcanizado", "4048"),
        ],
    )

    process_defaults = [
        ("moldeo", "Moldeo", "4032", 1, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("terminaciones", "Terminaciones", "4035", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("mecanizado", "Mecanizado", "4049", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("mecanizado_externo", "Mecanizado Externo", "4050", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("inspeccion_externa", "Inspeccion Externa", "4046", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("vulcanizado", "Vulcanizado", "4047", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("toma_dureza", "Toma de Dureza", "4035", 0, '{"libre_utilizacion": 0, "en_control_calidad": 1}'),
    ]
    con.executemany(
        """
        INSERT OR IGNORE INTO core_processes(process_id, label, sap_almacen, is_special_moldeo, availability_predicate_json)
        VALUES(?, ?, ?, ?, ?)
        """,
        process_defaults,
    )

    # Migration: Add required columns to core_material_master before part_code migration
    try:
        con.execute("ALTER TABLE core_material_master ADD COLUMN descripcion_material TEXT")
    except Exception:
        pass
    
    try:
        con.execute("ALTER TABLE core_material_master ADD COLUMN finish_days INTEGER DEFAULT 20")
    except Exception:
        pass
    
    try:
        con.execute("ALTER TABLE core_material_master ADD COLUMN min_finish_days INTEGER DEFAULT 5")
    except Exception:
        pass
    
    # Migration: Rename tiempo_enfriamiento_molde_dias to tiempo_enfriamiento_molde_horas (both store hours)
    try:
        con.execute("ALTER TABLE core_material_master RENAME COLUMN tiempo_enfriamiento_molde_dias TO tiempo_enfriamiento_molde_horas")
    except Exception:
        pass

    # Migration: Add cancha column to core_sap_demolding_snapshot
    try:
        con.execute("ALTER TABLE core_sap_demolding_snapshot ADD COLUMN cancha TEXT")
    except Exception:
        pass
    
    # Migration: Add part_code columns to demolding tables
    try:
        con.execute("ALTER TABLE core_moldes_por_fundir ADD COLUMN part_code TEXT")
    except Exception:
        pass
    
    try:
        con.execute("ALTER TABLE core_piezas_fundidas ADD COLUMN part_code TEXT")
    except Exception:
        pass
    
    # Note: mold_quantity should be REAL to store fractions (1/piezas_por_molde)
    # SQLite's INTEGER affinity can store REAL values, but for new tables we use REAL
    # Existing data will work correctly with float() conversion in Python
    
    # Migration: Refactor material_master to use part_code as PK (consolidates 4 material types)
    migrate_material_master_to_part_code(con)


def migrate_material_master_to_part_code(con: sqlite3.Connection) -> None:
    """Migrate core_material_master to use part_code (5 digits) as PK instead of material (11 digits).
    
    This consolidates multiple material codes (Pieza, Molde, Fundido, Trat.Term) into
    one record per part. Idempotent - safe to run multiple times.
    """
    # Check if already migrated (part_code column exists as PK)
    cursor = con.execute("PRAGMA table_info(core_material_master)")
    columns = {row[1]: row for row in cursor.fetchall()}
    
    # If part_code is already a column and material is not PK, skip migration
    if 'part_code' in columns:
        # Check if part_code is the PK (pk column = 1)
        if columns['part_code'][5] == 1:  # pk flag is at index 5
            return  # Already migrated
    
    # 1. Backup current table
    try:
        con.execute("DROP TABLE IF EXISTS _backup_material_master_20260206")
    except Exception:
        pass
    
    con.execute("""
        CREATE TABLE _backup_material_master_20260206 AS
        SELECT * FROM core_material_master
    """)
    
    # 2. Create new table with part_code as PK
    con.execute("""
        CREATE TABLE core_material_master_new (
            part_code TEXT PRIMARY KEY,
            descripcion_pieza TEXT,
            family_id TEXT,
            aleacion TEXT,
            flask_size TEXT,
            piezas_por_molde REAL,
            tiempo_enfriamiento_molde_horas INTEGER,
            finish_days INTEGER,
            min_finish_days INTEGER,
            vulcanizado_dias INTEGER,
            mecanizado_dias INTEGER,
            inspeccion_externa_dias INTEGER,
            peso_unitario_ton REAL,
            mec_perf_inclinada INTEGER NOT NULL DEFAULT 0,
            sobre_medida_mecanizado INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(family_id) REFERENCES core_family_catalog(family_id)
        )
    """)
    
    # 3. Migrate data - consolidate by part_code, keeping most recent/complete data
    # Use MAX aggregation to pick non-null values, prioritize Pieza materials
    con.execute("""
        INSERT INTO core_material_master_new
        SELECT
            part_code,
            MAX(descripcion_material) AS descripcion_pieza,
            MAX(family_id) AS family_id,
            MAX(aleacion) AS aleacion,
            MAX(flask_size) AS flask_size,
            MAX(piezas_por_molde) AS piezas_por_molde,
            MAX(tiempo_enfriamiento_molde_horas) AS tiempo_enfriamiento_molde_horas,
            MAX(finish_days) AS finish_days,
            MAX(min_finish_days) AS min_finish_days,
            MAX(vulcanizado_dias) AS vulcanizado_dias,
            MAX(mecanizado_dias) AS mecanizado_dias,
            MAX(inspeccion_externa_dias) AS inspeccion_externa_dias,
            MAX(peso_unitario_ton) AS peso_unitario_ton,
            MAX(mec_perf_inclinada) AS mec_perf_inclinada,
            MAX(sobre_medida_mecanizado) AS sobre_medida_mecanizado,
            MIN(created_at) AS created_at,
            MAX(updated_at) AS updated_at
        FROM (
            SELECT
                CASE
                    WHEN substr(material, 1, 2) = '40' AND substr(material, 5, 2) = '00' THEN substr(material, 7, 5)
                    WHEN substr(material, 1, 4) = '4310' AND substr(material, 10, 2) = '01' THEN substr(material, 5, 5)
                    WHEN substr(material, 1, 3) = '435' AND substr(material, 6, 1) = '0' THEN substr(material, 7, 5)
                    WHEN substr(material, 1, 3) = '436' AND substr(material, 6, 1) = '0' THEN substr(material, 7, 5)
                END AS part_code,
                *
            FROM core_material_master
        )
        WHERE part_code IS NOT NULL
        GROUP BY part_code
    """)
    
    # 4. Replace old table
    con.execute("DROP TABLE core_material_master")
    con.execute("ALTER TABLE core_material_master_new RENAME TO core_material_master")
    
    con.commit()


def seed_alloy_catalog(con: sqlite3.Connection) -> None:
    """Seed initial alloy codes if table is empty."""
    count = con.execute("SELECT COUNT(*) FROM core_alloy_catalog").fetchone()[0]
    if count > 0:
        return  # Already seeded
    
    initial_alloys = [
        ('32', 'CM2'),
        ('33', 'CM3'),
        ('34', 'CM4'),
        ('37', 'WS170'),
        ('38', 'CMHC'),
        ('42', 'CM6'),
        ('21', 'SP1'),
        ('28', 'SPX'),
    ]
    
    con.executemany("""
        INSERT INTO core_alloy_catalog (alloy_code, alloy_name, is_active)
        VALUES (?, ?, 1)
    """, initial_alloys)
    con.commit()
