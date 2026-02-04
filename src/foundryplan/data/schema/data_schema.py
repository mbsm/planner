from __future__ import annotations

import sqlite3


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT(datetime('now', 'localtime')),
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            details TEXT
        );

        CREATE TABLE IF NOT EXISTS family_catalog (
            family_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS app_config (
            config_key TEXT PRIMARY KEY,
            config_value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS material_master (
            material TEXT PRIMARY KEY,
            family_id TEXT,
            aleacion TEXT,
            flask_size TEXT,
            piezas_por_molde REAL,
            tiempo_enfriamiento_molde_dias INTEGER,
            finish_hours REAL,
            min_finish_hours REAL,
            vulcanizado_dias INTEGER,
            mecanizado_dias INTEGER,
            inspeccion_externa_dias INTEGER,
            peso_unitario_ton REAL,
            mec_perf_inclinada INTEGER NOT NULL DEFAULT 0,
            sobre_medida_mecanizado INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(family_id) REFERENCES family_catalog(family_id)
        );

        CREATE TABLE IF NOT EXISTS process (
            process_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            sap_almacen TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            is_special_moldeo INTEGER NOT NULL DEFAULT 0,
            availability_predicate_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS resource (
            resource_id TEXT PRIMARY KEY,
            process_id TEXT NOT NULL,
            name TEXT NOT NULL,
            capacity_per_day REAL,
            sort_order INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(process_id) REFERENCES process(process_id)
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
            FOREIGN KEY(process_id) REFERENCES process(process_id)
        );

        CREATE TABLE IF NOT EXISTS sap_mb52_snapshot (
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

        CREATE TABLE IF NOT EXISTS sap_vision_snapshot (
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

        CREATE VIEW IF NOT EXISTS sap_vision AS SELECT * FROM sap_vision_snapshot;

        CREATE TABLE IF NOT EXISTS sap_demolding_snapshot (
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
            mold_quantity INTEGER
        );

        CREATE TABLE IF NOT EXISTS orders (
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
        """
    )

    con.executemany(
        "INSERT OR IGNORE INTO family_catalog(family_id, label) VALUES(?, ?)",
        [
            ("Parrillas", "Parrillas"),
            ("Lifters", "Lifters"),
            ("Corazas", "Corazas"),
            ("Otros", "Otros"),
            ("No pieza", "No pieza"),
        ],
    )

    con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_centro', '4000')")
    con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_material_prefixes', '401,402,403,404')")
    con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_vision_material_prefixes', '401,402,403,404')")
    con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('job_priority_map', '{\"prueba\": 1, \"urgente\": 2, \"normal\": 3}')")
    con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('planner_horizon_days', '30')")
    con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('planner_horizon_buffer_days', '10')")
    con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('planner_holidays', '')")

    con.executemany(
        "INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES(?, ?)",
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
        ("terminaciones", "Terminaciones", "4035", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("toma_de_dureza", "Toma de dureza", "4035", 0, '{"libre_utilizacion": 0, "en_control_calidad": 1}'),
        ("mecanizado", "Mecanizado", "4049", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("mecanizado_externo", "Mecanizado externo", "4050", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("inspeccion_externa", "Inspección externa", "4046", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("por_vulcanizar", "Por vulcanizar", "4047", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("en_vulcanizado", "En vulcanizado", "4048", 0, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
        ("moldeo", "Moldeo", "4032", 1, '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
    ]
    con.executemany(
        """
        INSERT OR IGNORE INTO process(process_id, label, sap_almacen, is_special_moldeo, availability_predicate_json)
        VALUES(?, ?, ?, ?, ?)
        """,
        process_defaults,
    )

    # Migration: Rename finish_hours to finish_days, min_finish_hours to min_finish_days
    # SQLite doesn't support column rename directly, so we add new columns and copy data
    try:
        con.execute("ALTER TABLE material_master ADD COLUMN finish_days INTEGER DEFAULT 15")
    except Exception:
        pass
    
    try:
        con.execute("ALTER TABLE material_master ADD COLUMN min_finish_days INTEGER DEFAULT 5")
    except Exception:
        pass
    
    # Copy data from old columns to new (if old columns exist and new are empty)
    try:
        con.execute("""
            UPDATE material_master 
            SET finish_days = CAST(finish_hours / 24.0 AS INTEGER)
            WHERE finish_hours IS NOT NULL AND finish_days IS NULL
        """)
    except Exception:
        pass
    
    try:
        con.execute("""
            UPDATE material_master 
            SET min_finish_days = CAST(min_finish_hours / 24.0 AS INTEGER)
            WHERE min_finish_hours IS NOT NULL AND min_finish_days IS NULL
        """)
    except Exception:
        pass
    
    # Migration: Add cancha column to sap_demolding_snapshot
    try:
        con.execute("ALTER TABLE sap_demolding_snapshot ADD COLUMN cancha TEXT")
    except Exception:
        pass
