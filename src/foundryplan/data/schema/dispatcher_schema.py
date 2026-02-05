from __future__ import annotations

import sqlite3


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS dispatcher_job (
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
            FOREIGN KEY(process_id) REFERENCES core_processes(process_id),
            FOREIGN KEY(material) REFERENCES core_material_master(material)
        );

        CREATE TABLE IF NOT EXISTS dispatcher_job_unit (
            job_unit_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            lote TEXT NOT NULL,
            correlativo_int INTEGER,
            qty INTEGER NOT NULL DEFAULT 1,
            status TEXT DEFAULT 'available',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(job_id) REFERENCES dispatcher_job(job_id)
        );

        CREATE TABLE IF NOT EXISTS dispatcher_line_config (
            process TEXT NOT NULL,
            line_id INTEGER NOT NULL,
            line_name TEXT,
            families_json TEXT NOT NULL,
            mec_perf_inclinada INTEGER DEFAULT 0,
            sobre_medida_mecanizado INTEGER DEFAULT 0,
            PRIMARY KEY(process, line_id)
        );

        CREATE TABLE IF NOT EXISTS dispatcher_last_program (
            process TEXT PRIMARY KEY,
            generated_on TEXT NOT NULL,
            program_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dispatcher_program_in_progress (
            process TEXT NOT NULL,
            pedido TEXT NOT NULL,
            posicion TEXT NOT NULL,
            is_test INTEGER NOT NULL DEFAULT 0,
            line_id INTEGER NOT NULL,
            marked_at TEXT NOT NULL,
            PRIMARY KEY (process, pedido, posicion, is_test)
        );

        CREATE TABLE IF NOT EXISTS dispatcher_program_in_progress_item (
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

        CREATE TABLE IF NOT EXISTS dispatcher_order_priority (
            pedido TEXT PRIMARY KEY,
            is_priority INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS dispatcher_orderpos_priority (
            pedido TEXT NOT NULL,
            posicion TEXT NOT NULL,
            is_priority INTEGER NOT NULL DEFAULT 0,
            kind TEXT,
            PRIMARY KEY (pedido, posicion)
        );
        """
    )
    # Migrations: Add columns if they don't exist
    try:
        con.execute("ALTER TABLE dispatcher_line_config ADD COLUMN mec_perf_inclinada INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    try:
        con.execute("ALTER TABLE dispatcher_line_config ADD COLUMN sobre_medida_mecanizado INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Column already exists