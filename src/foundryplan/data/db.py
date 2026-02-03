from __future__ import annotations


from contextlib import contextmanager
import sqlite3
from pathlib import Path


class Db:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=20.0)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def ensure_schema(self) -> None:
        # Initial connection to set mode and tables
        con = sqlite3.connect(self.path, timeout=10.0)
        try:
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA foreign_keys=ON;")
            
            # Migration pre-check: job_unit missing PK?
            # Existing `job_unit` table from older schema might lack job_unit_id.
            # Use raw query since _table_exists might be defined later or strict.
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_unit'").fetchone():
                cols = [r[1] for r in con.execute("PRAGMA table_info(job_unit)").fetchall()]
                if "job_unit_id" not in cols:
                    # Drop it so it gets recreated correctly below
                    con.execute("DROP TABLE job_unit")

            # AUDIT LOG
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL DEFAULT(datetime('now', 'localtime')),
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT
                );
                """
            )

            # FASE 1.1: Tablas de Configuración Base
            con.executescript(
                """
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

                -- FASE 1.2: Tablas SAP Staging (snapshot-based)
                CREATE TABLE IF NOT EXISTS sap_mb52_snapshot (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    loaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    material TEXT NOT NULL,
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

                -- FASE 1.3: Tablas de Jobs
                CREATE TABLE IF NOT EXISTS job (
                    job_id TEXT PRIMARY KEY,
                    process_id TEXT NOT NULL,
                    pedido TEXT NOT NULL,
                    posicion TEXT NOT NULL,
                    material TEXT NOT NULL,
                    qty INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER,
                    is_test INTEGER NOT NULL DEFAULT 0,
                    state TEXT DEFAULT 'pending',
                    fecha_de_pedido TEXT,
                    notes TEXT,
                    corr_min INTEGER,
                    corr_max INTEGER,
                    cliente TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(process_id) REFERENCES process(process_id),
                    FOREIGN KEY(material) REFERENCES material_master(material)
                );

                CREATE TABLE IF NOT EXISTS job_unit (
                    job_unit_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    lote TEXT NOT NULL,
                    correlativo_int INTEGER,
                    qty INTEGER NOT NULL DEFAULT 1,
                    status TEXT DEFAULT 'available',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(job_id) REFERENCES job(job_id)
                );

                -- FASE 1.4: Tablas de Dispatch
                CREATE TABLE IF NOT EXISTS dispatch_queue_run (
                    run_id TEXT PRIMARY KEY,
                    process_id TEXT NOT NULL,
                    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    algo_version TEXT,
                    FOREIGN KEY(process_id) REFERENCES process(process_id)
                );

                CREATE TABLE IF NOT EXISTS dispatch_queue_item (
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    resource_id TEXT NOT NULL,
                    job_id TEXT,
                    qty INTEGER,
                    split_id INTEGER NOT NULL DEFAULT 1,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(run_id, seq),
                    FOREIGN KEY(run_id) REFERENCES dispatch_queue_run(run_id),
                    FOREIGN KEY(resource_id) REFERENCES resource(resource_id),
                    FOREIGN KEY(job_id) REFERENCES job(job_id)
                );

                CREATE TABLE IF NOT EXISTS last_dispatch (
                    process_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(process_id) REFERENCES process(process_id),
                    FOREIGN KEY(run_id) REFERENCES dispatch_queue_run(run_id)
                );

                CREATE TABLE IF NOT EXISTS dispatch_in_progress (
                    process_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(process_id) REFERENCES process(process_id),
                    FOREIGN KEY(run_id) REFERENCES dispatch_queue_run(run_id)
                );

                CREATE TABLE IF NOT EXISTS dispatch_in_progress_item (
                    process_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    job_id TEXT,
                    split_id INTEGER NOT NULL DEFAULT 1,
                    qty_assigned INTEGER,
                    marked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(process_id, resource_id, job_id, split_id),
                    FOREIGN KEY(process_id) REFERENCES process(process_id),
                    FOREIGN KEY(resource_id) REFERENCES resource(resource_id),
                    FOREIGN KEY(job_id) REFERENCES job(job_id)
                );

                -- FASE 1.5-1.6: Tablas de Estado & KPI (Legacy + New)
                CREATE TABLE IF NOT EXISTS vision_kpi_daily (
                    snapshot_date TEXT PRIMARY KEY,
                    snapshot_at TEXT NOT NULL,
                    tons_por_entregar REAL NOT NULL,
                    tons_atrasadas REAL NOT NULL
                );

                -- ===== Planner tables (moldeo planner) =====
                CREATE TABLE IF NOT EXISTS planner_scenarios (
                    scenario_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS planner_parts (
                    scenario_id INTEGER NOT NULL,
                    part_id TEXT NOT NULL,
                    flask_size TEXT CHECK(flask_size IN ('S','M','L')),
                    cool_hours REAL,
                    finish_hours REAL,
                    min_finish_hours REAL,
                    pieces_per_mold REAL,
                    net_weight_ton REAL,
                    alloy TEXT,
                    PRIMARY KEY (scenario_id, part_id)
                );

                CREATE TABLE IF NOT EXISTS planner_orders (
                    scenario_id INTEGER NOT NULL,
                    order_id TEXT NOT NULL,
                    part_id TEXT NOT NULL,
                    qty INTEGER,
                    due_date TEXT,
                    priority INTEGER DEFAULT 100,
                    PRIMARY KEY (scenario_id, order_id)
                );

                CREATE TABLE IF NOT EXISTS planner_resources (
                    scenario_id INTEGER PRIMARY KEY,
                    flasks_S INTEGER,
                    flasks_M INTEGER,
                    flasks_L INTEGER,
                    molding_max_per_day INTEGER,
                    molding_max_same_part_per_day INTEGER,
                    pour_max_ton_per_day REAL,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS planner_calendar_workdays (
                    scenario_id INTEGER NOT NULL,
                    workday_index INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    week_index INTEGER NOT NULL,
                    PRIMARY KEY (scenario_id, workday_index),
                    UNIQUE (scenario_id, date)
                );

                CREATE TABLE IF NOT EXISTS planner_initial_order_progress (
                    scenario_id INTEGER NOT NULL,
                    asof_date TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    remaining_molds INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS planner_initial_patterns_loaded (
                    scenario_id INTEGER NOT NULL,
                    asof_date TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    is_loaded INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS planner_initial_flask_inuse (
                    scenario_id INTEGER NOT NULL,
                    asof_date TEXT NOT NULL,
                    flask_size TEXT CHECK(flask_size IN ('S','M','L')),
                    release_workday_index INTEGER NOT NULL,
                    qty_inuse INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS planner_initial_pour_load (
                    scenario_id INTEGER NOT NULL,
                    asof_date TEXT NOT NULL,
                    workday_index INTEGER NOT NULL,
                    tons_committed REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS planner_plan_daily_order (
                    scenario_id INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    asof_date TEXT NOT NULL,
                    workday_index INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    part_id TEXT NOT NULL,
                    molds_molded INTEGER NOT NULL,
                    PRIMARY KEY (scenario_id, run_id, workday_index, order_id)
                );

                CREATE TABLE IF NOT EXISTS planner_plan_weekly_order (
                    scenario_id INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    asof_date TEXT NOT NULL,
                    week_index INTEGER NOT NULL,
                    order_id TEXT NOT NULL,
                    part_id TEXT NOT NULL,
                    molds_molded_week INTEGER NOT NULL,
                    PRIMARY KEY (scenario_id, run_id, week_index, order_id)
                );

                CREATE TABLE IF NOT EXISTS planner_order_status (
                    scenario_id INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    asof_date TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    part_id TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    remaining_molds INTEGER NOT NULL,
                    remaining_qty INTEGER NOT NULL,
                    due_date TEXT NOT NULL,
                    delivered_by_due INTEGER NOT NULL,
                    late_qty INTEGER NOT NULL,
                    completion_workday_index INTEGER,
                    PRIMARY KEY (scenario_id, run_id, order_id)
                );

                -- Legacy program tables (backward compat)
                CREATE TABLE IF NOT EXISTS program_in_progress (
                    process TEXT NOT NULL,
                    pedido TEXT NOT NULL,
                    posicion TEXT NOT NULL,
                    is_test INTEGER NOT NULL DEFAULT 0,
                    line_id INTEGER NOT NULL,
                    marked_at TEXT NOT NULL,
                    PRIMARY KEY (process, pedido, posicion, is_test)
                );

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

            # job table v2: rename numero_parte -> material
            if self._table_exists(con, "job"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(job)").fetchall()]
                if "numero_parte" in cols and "material" not in cols:
                    try:
                        con.execute("ALTER TABLE job RENAME COLUMN numero_parte TO material")
                    except Exception:
                        pass
                
                # Re-fetch columns
                cols = [r[1] for r in con.execute("PRAGMA table_info(job)").fetchall()]
                if "fecha_de_pedido" not in cols:
                    try:
                        con.execute("ALTER TABLE job ADD COLUMN fecha_de_pedido TEXT")
                    except Exception:
                        pass
                if "notes" not in cols:
                    try:
                        con.execute("ALTER TABLE job ADD COLUMN notes TEXT")
                    except Exception:
                        pass

            # Migrations for V0.2 (Jobs)
            try:
                con.execute("ALTER TABLE job ADD COLUMN corr_min INTEGER")
            except sqlite3.OperationalError:
                pass
            try:
                con.execute("ALTER TABLE job ADD COLUMN corr_max INTEGER")
            except sqlite3.OperationalError:
                pass
            # Job qty v0.3: add qty and backfill from job_unit count or legacy qty_total
            if self._table_exists(con, "job"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(job)").fetchall()]
                if "qty" not in cols:
                    try:
                        con.execute("ALTER TABLE job ADD COLUMN qty INTEGER NOT NULL DEFAULT 0")
                    except Exception:
                        pass
                # Backfill qty for existing rows (prefer job_unit count)
                cols = [r[1] for r in con.execute("PRAGMA table_info(job)").fetchall()]
                if "qty" in cols:
                    try:
                        con.execute(
                            """
                            UPDATE job
                            SET qty = COALESCE(
                                (SELECT COUNT(*) FROM job_unit ju WHERE ju.job_id = job.job_id),
                                qty,
                                0
                            )
                            """
                        )
                    except Exception:
                        pass

            # Planner schema updates
            if self._table_exists(con, "material_master"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(material_master)").fetchall()]
                if "finish_hours" not in cols:
                    try:
                        con.execute("ALTER TABLE material_master ADD COLUMN finish_hours REAL")
                    except Exception:
                        pass
                if "min_finish_hours" not in cols:
                    try:
                        con.execute("ALTER TABLE material_master ADD COLUMN min_finish_hours REAL")
                    except Exception:
                        pass

            if self._table_exists(con, "planner_parts"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(planner_parts)").fetchall()]
                if "min_finish_hours" not in cols:
                    try:
                        con.execute("ALTER TABLE planner_parts ADD COLUMN min_finish_hours REAL")
                    except Exception:
                        pass
                if "pieces_per_mold" not in cols:
                    try:
                        con.execute("ALTER TABLE planner_parts ADD COLUMN pieces_per_mold REAL")
                    except Exception:
                        pass

            if self._table_exists(con, "planner_initial_order_progress"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(planner_initial_order_progress)").fetchall()]
                if "remaining_molds" not in cols:
                    try:
                        con.execute("ALTER TABLE planner_initial_order_progress ADD COLUMN remaining_molds INTEGER NOT NULL DEFAULT 0")
                    except Exception:
                        pass

            if self._table_exists(con, "planner_order_status"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(planner_order_status)").fetchall()]
                if "remaining_molds" not in cols:
                    try:
                        con.execute("ALTER TABLE planner_order_status ADD COLUMN remaining_molds INTEGER NOT NULL DEFAULT 0")
                    except Exception:
                        pass

            # Remove fecha_entrega columns (v0.3) by rebuilding tables where needed.
            try:
                # sap_vision_snapshot: drop fecha_entrega if present
                cols = [r[1] for r in con.execute("PRAGMA table_info(sap_vision_snapshot)").fetchall()]
                if "fecha_entrega" in cols:
                    con.execute("DROP VIEW IF EXISTS sap_vision")
                    con.execute("ALTER TABLE sap_vision_snapshot RENAME TO sap_vision_snapshot_old")
                    con.execute(
                        """
                        CREATE TABLE sap_vision_snapshot (
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
                        """.strip()
                    )
                    con.execute(
                        """
                        INSERT INTO sap_vision_snapshot(
                            snapshot_id, loaded_at, pedido, posicion, tipo_posicion, tipo_de_reparto, cliente,
                            n_oc_cliente, pos_oc, material_client_code, cod_material, descripcion_material,
                            atributo, fecha_de_pedido, solicitado, x_programar, programado, x_fundir, desmoldeo, tt,
                            terminacion, mecanizado_interno, mecanizado_externo, vulcanizado, en_vulcaniz,
                            pend_vulcanizado, insp_externa, rech_insp_externa, lib_vulcaniz_de, bodega, despachado,
                            rechazo, ret_qm, grupo_art, proveedor, status, status_comercial, jerarquia_productos,
                            peso_neto_ton, peso_unitario_ton
                        )
                        SELECT
                            snapshot_id, loaded_at, pedido, posicion, tipo_posicion, tipo_de_reparto, cliente,
                            n_oc_cliente, pos_oc, material_client_code, cod_material, descripcion_material,
                            atributo, fecha_de_pedido, solicitado, x_programar, programado, x_fundir, desmoldeo, tt,
                            terminacion, mecanizado_interno, mecanizado_externo, vulcanizado, en_vulcaniz,
                            pend_vulcanizado, insp_externa, rech_insp_externa, lib_vulcaniz_de, bodega, despachado,
                            rechazo, ret_qm, grupo_art, proveedor, status, status_comercial, jerarquia_productos,
                            peso_neto_ton, peso_unitario_ton
                        FROM sap_vision_snapshot_old
                        """.strip()
                    )
                    con.execute("DROP TABLE sap_vision_snapshot_old")
                    con.execute("CREATE VIEW IF NOT EXISTS sap_vision AS SELECT * FROM sap_vision_snapshot")

                # job: rename fecha_entrega -> fecha_de_pedido if needed
                cols = [r[1] for r in con.execute("PRAGMA table_info(job)").fetchall()]
                if "fecha_entrega" in cols and "fecha_de_pedido" not in cols:
                    con.execute("ALTER TABLE job RENAME TO job_old")
                    con.execute(
                        """
                        CREATE TABLE job (
                            job_id TEXT PRIMARY KEY,
                            process_id TEXT NOT NULL,
                            pedido TEXT NOT NULL,
                            posicion TEXT NOT NULL,
                            material TEXT NOT NULL,
                            qty INTEGER NOT NULL DEFAULT 0,
                            priority INTEGER,
                            is_test INTEGER NOT NULL DEFAULT 0,
                            state TEXT DEFAULT 'pending',
                            fecha_de_pedido TEXT,
                            notes TEXT,
                            corr_min INTEGER,
                            corr_max INTEGER,
                            cliente TEXT,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY(process_id) REFERENCES process(process_id),
                            FOREIGN KEY(material) REFERENCES material_master(material)
                        );
                        """.strip()
                    )
                    con.execute(
                        """
                        INSERT INTO job(
                            job_id, process_id, pedido, posicion, material, qty, priority, is_test, state,
                            fecha_de_pedido, notes, corr_min, corr_max, cliente, created_at, updated_at
                        )
                        SELECT
                            job_id, process_id, pedido, posicion, material,
                            COALESCE(qty, 0),
                            priority, is_test, state,
                            fecha_entrega,
                            notes, corr_min, corr_max, cliente, created_at, updated_at
                        FROM job_old
                        """.strip()
                    )
                    con.execute("DROP TABLE job_old")

                # orders: rename fecha_entrega -> fecha_de_pedido if needed
                cols = [r[1] for r in con.execute("PRAGMA table_info(orders)").fetchall()]
                if "fecha_entrega" in cols and "fecha_de_pedido" not in cols:
                    con.execute("ALTER TABLE orders RENAME TO orders_old")
                    con.execute(
                        """
                        CREATE TABLE orders (
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
                        """.strip()
                    )
                    con.execute(
                        """
                        INSERT INTO orders(
                            process, almacen, pedido, posicion, material, cantidad, fecha_de_pedido,
                            primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test, cliente
                        )
                        SELECT
                            process, almacen, pedido, posicion, material, cantidad,
                            fecha_entrega,
                            primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test, cliente
                        FROM orders_old
                        """.strip()
                    )
                    con.execute("DROP TABLE orders_old")
            except Exception:
                # Best-effort migrations should not prevent startup.
                pass

            # ===== NOTA: No backward compatibility - solo tablas v0.2 =====
            # No migrations from legacy tables

            # sap_vision_snapshot v2: add optional weight fields (tons) if migrating old schema
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

            # parts table v2-v4: add optional columns (only if table exists - legacy support)
            if self._table_exists(con, "parts"):
                part_cols = [r[1] for r in con.execute("PRAGMA table_info(parts)").fetchall()]
                if "vulcanizado_dias" not in part_cols:
                    try:
                        con.execute("ALTER TABLE parts ADD COLUMN vulcanizado_dias INTEGER")
                    except Exception:
                        pass
                if "mecanizado_dias" not in part_cols:
                    try:
                        con.execute("ALTER TABLE parts ADD COLUMN mecanizado_dias INTEGER")
                    except Exception:
                        pass
                if "inspeccion_externa_dias" not in part_cols:
                    try:
                        con.execute("ALTER TABLE parts ADD COLUMN inspeccion_externa_dias INTEGER")
                    except Exception:
                        pass
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

            # material_master: add flask_size if missing
            if self._table_exists(con, "material_master"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(material_master)").fetchall()]
                if "flask_size" not in cols:
                    try:
                        con.execute("ALTER TABLE material_master ADD COLUMN flask_size TEXT")
                    except Exception:
                        pass

            # Migrate app_config v1->v2 (key->config_key, value->config_value)
            if self._table_exists(con, "app_config"):
                app_cols = [r[1] for r in con.execute("PRAGMA table_info(app_config)").fetchall()]
                if "config_key" not in app_cols and "key" in app_cols:
                    try:
                        con.execute("ALTER TABLE app_config RENAME COLUMN key TO config_key")
                        con.execute("ALTER TABLE app_config RENAME COLUMN value TO config_value")
                    except Exception:
                        pass
                
                # Re-fetch columns after potential rename
                app_cols = [r[1] for r in con.execute("PRAGMA table_info(app_config)").fetchall()]
                if "updated_at" not in app_cols:
                    try:
                        con.execute("ALTER TABLE app_config ADD COLUMN updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
                    except Exception:
                        pass

            # Seed default catalog entries only if catalog is empty.
            families_count = int(con.execute("SELECT COUNT(*) FROM family_catalog").fetchone()[0])
            if families_count == 0:
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

            # Ensure special catalog entries exist even on existing databases.
            con.execute("INSERT OR IGNORE INTO family_catalog(family_id, label) VALUES(?, ?)", ("No pieza", "No pieza"))

            # Migrate any existing familias from material_master into the catalog.
            con.execute(
                "INSERT OR IGNORE INTO family_catalog(family_id, label) SELECT DISTINCT family_id, family_id FROM material_master WHERE family_id IS NOT NULL AND TRIM(family_id) <> ''"
            )

            # Seed default SAP config values if missing (v0.2 uses config_key/config_value).
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_center', '4000')")
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_material_prefixes', '436')")
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('job_priority_map', '{\"prueba\": 1, \"urgente\": 2, \"normal\": 3}')")

            # Process warehouse mapping (by process_id + sap_almacen).
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_almacen_moldeo', '4032')")
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_almacen_terminaciones', '4035')")
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_almacen_toma_dureza', '4035')")
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_almacen_mecanizado', '4049')")
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_almacen_mecanizado_externo', '4050')")
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_almacen_inspeccion_externa', '4046')")
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_almacen_por_vulcanizar', '4047')")
            con.execute("INSERT OR IGNORE INTO app_config(config_key, config_value) VALUES('sap_almacen_en_vulcanizado', '4048')")

            # Seed default processes if table is empty
            process_count = int(con.execute("SELECT COUNT(*) FROM process").fetchone()[0])
            if process_count == 0:
                con.executemany(
                    """INSERT OR IGNORE INTO process(process_id, label, sap_almacen, is_active, is_special_moldeo)
                       VALUES(?, ?, ?, 1, ?)""",
                    [
                        ("moldeo", "Moldeo", None, 1),
                        ("terminaciones", "Terminaciones", "4035", 0),
                        ("mecanizado", "Mecanizado", "4049", 0),
                        ("mecanizado_externo", "Mecanizado Externo", "4050", 0),
                        ("inspeccion_externa", "Inspección Externa", "4046", 0),
                        ("vulcanizado", "Vulcanizado", "4047", 0),
                        ("toma_dureza", "Toma de Dureza", "4035", 0),
                    ],
                )

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
                                material TEXT NOT NULL,
                                cantidad INTEGER NOT NULL,
                                fecha_de_pedido TEXT NOT NULL,
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
                                "INSERT OR IGNORE INTO orders(process, almacen, pedido, posicion, numero_parte, cantidad, fecha_de_pedido, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test) "
                                f"SELECT 'terminaciones', ?, pedido, posicion, numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, {is_test_expr} FROM orders_old",
                                (almacen_term,),
                            )
                        else:
                            # Legacy (no posicion): keep placeholder.
                            con.execute(
                                "INSERT OR IGNORE INTO orders(process, almacen, pedido, posicion, numero_parte, cantidad, fecha_de_pedido, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test) "
                                "SELECT 'terminaciones', ?, pedido, '0000', numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, 0 FROM orders_old",
                                (almacen_term,),
                            )
                    except Exception:
                        # Best-effort migrations should not prevent startup.
                        pass

            # parts table v5: rename numero_parte -> material if somehow missed
            if self._table_exists(con, "parts"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(parts)").fetchall()]
                if "numero_parte" in cols and "material" not in cols:
                    try:
                        con.execute("ALTER TABLE parts RENAME COLUMN numero_parte TO material")
                    except Exception:
                        pass

            # orders table v5: rename numero_parte -> material if somehow missed
            if self._table_exists(con, "orders"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(orders)").fetchall()]
                if "numero_parte" in cols and "material" not in cols:
                    try:
                        con.execute("ALTER TABLE orders RENAME COLUMN numero_parte TO material")
                    except Exception:
                        pass

            # orders table v6: add cliente column
            if self._table_exists(con, "orders"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(orders)").fetchall()]
                if "cliente" not in cols:
                    try:
                        con.execute("ALTER TABLE orders ADD COLUMN cliente TEXT")
                    except Exception:
                        pass

            # job table: add cliente column
            if self._table_exists(con, "job"):
                cols = [r[1] for r in con.execute("PRAGMA table_info(job)").fetchall()]
                if "cliente" not in cols:
                    try:
                        con.execute("ALTER TABLE job ADD COLUMN cliente TEXT")
                    except Exception:
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
            
            con.commit()
        finally:
            con.close()

    def _table_exists(self, con: sqlite3.Connection, table_name: str) -> bool:
        """Check if a table exists in the database."""
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None
