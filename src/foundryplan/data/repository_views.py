from __future__ import annotations

from typing import Any

from foundryplan.data.db import Db


class DataRepository:
    """Data module access: SAP snapshots, material master, and general config."""

    def __init__(self, db: Db) -> None:
        from foundryplan.data.data_repository import DataRepositoryImpl
        self._repo = DataRepositoryImpl(db=db)

    @property
    def processes(self) -> dict[str, dict[str, str]]:
        return self._repo.processes

    # SAP snapshots
    def count_sap_mb52(self) -> int:
        return self._repo.count_sap_mb52()

    def count_sap_vision(self) -> int:
        return self._repo.count_sap_vision()

    def count_sap_demolding(self) -> int:
        return self._repo.count_sap_demolding()

    def import_sap_mb52_bytes(self, *, content: bytes, mode: str = "replace") -> None:
        return self._repo.import_sap_mb52_bytes(content=content, mode=mode)

    def import_sap_vision_bytes(self, *, content: bytes) -> None:
        return self._repo.import_sap_vision_bytes(content=content)

    def import_excel_bytes(self, *, kind: str, content: bytes) -> int:
        return self._repo.import_excel_bytes(kind=kind, content=content)

    def import_sap_demolding_bytes(self, *, content: bytes) -> int:
        return self._repo.import_sap_demolding_bytes(content=content)

    def clear_imported_data(self) -> None:
        return self._repo.clear_imported_data()

    # Orders & stock-derived views
    def try_rebuild_orders_from_sap_for(self, *, process: str = "terminaciones") -> bool:
        return self._repo.try_rebuild_orders_from_sap_for(process=process)

    def rebuild_orders_from_sap_for(self, *, process: str = "terminaciones") -> int:
        return self._repo.rebuild_orders_from_sap_for(process=process)

    def get_orders_model(self, *, process: str = "terminaciones") -> list[Any]:
        return self._repo.get_orders_model(process=process)

    def count_orders(self, *, process: str = "terminaciones") -> int:
        return self._repo.count_orders(process=process)

    def count_missing_parts_from_orders(self, *, process: str = "terminaciones") -> int:
        return self._repo.count_missing_parts_from_orders(process=process)

    def count_missing_process_times_from_orders(self, *, process: str = "terminaciones") -> int:
        return self._repo.count_missing_process_times_from_orders(process=process)

    def get_orders_overdue_rows(self, *, today=None, limit: int = 200) -> list[dict]:
        return self._repo.get_orders_overdue_rows(today=today, limit=limit)

    def get_orders_due_soon_rows(self, *, days: int = 49, limit: int = 200) -> list[dict]:
        return self._repo.get_orders_due_soon_rows(days=days, limit=limit)

    def get_orders_rows(self, limit: int = 200) -> list[dict]:
        return self._repo.get_orders_rows(limit=limit)

    def get_pedidos_master_rows(self) -> list[dict]:
        return self._repo.get_pedidos_master_rows()

    def get_vision_stage_breakdown(self, *, pedido: str, posicion: str) -> dict:
        return self._repo.get_vision_stage_breakdown(pedido=pedido, posicion=posicion)

    # Material master
    def get_parts_rows(self) -> list[dict]:
        return self._repo.get_parts_rows()

    def upsert_part_master(self, **kwargs) -> None:
        return self._repo.upsert_part_master(**kwargs)

    def upsert_part(self, **kwargs) -> None:
        return self._repo.upsert_part(**kwargs)

    def delete_part(self, *, material: str) -> None:
        return self._repo.delete_part(material=material)

    def delete_all_parts(self) -> None:
        return self._repo.delete_all_parts()

    def list_families(self) -> list[str]:
        return self._repo.list_families()

    def get_families_rows(self) -> list[dict]:
        return self._repo.get_families_rows()

    def add_family(self, *, name: str) -> None:
        return self._repo.add_family(name=name)

    def rename_family(self, *, old: str, new: str) -> None:
        return self._repo.rename_family(old=old, new=new)

    def delete_family(self, *, name: str, force: bool = False) -> None:
        return self._repo.delete_family(name=name, force=force)

    # General config + KPI
    def get_config(self, *, key: str, default: str | None = None) -> str | None:
        return self._repo.get_config(key=key, default=default)

    def set_config(self, *, key: str, value: str) -> None:
        return self._repo.set_config(key=key, value=value)

    def get_process_config(self, *, process_id: str) -> dict | None:
        return self._repo.get_process_config(process_id=process_id)

    def update_process_config(
        self,
        *,
        process_id: str,
        label: str | None = None,
        sap_almacen: str | None = None,
        availability_predicate_json: str | None = None,
    ) -> None:
        return self._repo.update_process_config(
            process_id=process_id,
            label=label,
            sap_almacen=sap_almacen,
            availability_predicate_json=availability_predicate_json,
        )

    def upsert_vision_kpi_daily(self, *, snapshot_date=None) -> dict:
        return self._repo.upsert_vision_kpi_daily(snapshot_date=snapshot_date)

    def get_vision_kpi_daily_rows(self, *, limit: int = 120) -> list[dict]:
        return self._repo.get_vision_kpi_daily_rows(limit=limit)

    def get_mb52_snapshot_sample(self, *, limit: int = 100) -> list[dict]:
        return self._repo.get_mb52_snapshot_sample(limit=limit)

    def get_vision_snapshot_sample(self, *, limit: int = 100) -> list[dict]:
        return self._repo.get_vision_snapshot_sample(limit=limit)

    def get_demolding_snapshot_sample(self, *, limit: int = 100) -> list[dict]:
        return self._repo.get_demolding_snapshot_sample(limit=limit)

    def get_mb52_texto_breve(self, *, material: str) -> str:
        return self._repo.get_mb52_texto_breve(material=material)

    def get_sap_rebuild_diagnostics(self, *, process: str | None = None) -> dict:
        return self._repo.get_sap_rebuild_diagnostics(process=process)

    def get_sap_mb52_almacen_counts(self, *, centro: str, limit: int = 50) -> list[dict]:
        return self._repo.get_sap_mb52_almacen_counts(centro=centro, limit=limit)

    def get_sap_orderpos_missing_vision_rows(self, *, limit: int = 200) -> list[dict]:
        return self._repo.get_sap_orderpos_missing_vision_rows(limit=limit)

    def get_sap_non_usable_with_orderpos_rows(self, *, limit: int = 200) -> list[dict]:
        return self._repo.get_sap_non_usable_with_orderpos_rows(limit=limit)

    def get_missing_parts_from_mb52_for(self, *, process: str) -> list[dict]:
        return self._repo.get_missing_parts_from_mb52_for(process=process)

    def get_missing_parts_from_vision_for(self) -> list[dict]:
        return self._repo.get_missing_parts_from_vision_for()

    def list_db_tables(self) -> list[str]:
        return self._repo.list_db_tables()

    def count_table_rows(self, *, table: str) -> int:
        return self._repo.count_table_rows(table=table)

    def fetch_table_rows(self, *, table: str, limit: int, offset: int = 0) -> list[dict]:
        return self._repo.fetch_table_rows(table=table, limit=limit, offset=offset)

    def get_recent_audit_entries(self, limit: int = 100) -> list[Any]:
        return self._repo.get_recent_audit_entries(limit=limit)


class DispatcherRepository:
    """Dispatcher module access: job/program tables and line configuration."""

    def __init__(self, db: Db, *, data: DataRepository) -> None:
        from foundryplan.dispatcher.dispatcher_repository import DispatcherRepositoryImpl
        self._repo = DispatcherRepositoryImpl(db=db, data_repo=data._repo)
        self.data = data

    def count_orders(self, *, process: str = "terminaciones") -> int:
        return self._repo.count_orders(process=process)

    def count_missing_parts_from_orders(self, *, process: str = "terminaciones") -> int:
        return self._repo.count_missing_parts_from_orders(process=process)

    def count_missing_process_times_from_orders(self, *, process: str = "terminaciones") -> int:
        return self._repo.count_missing_process_times_from_orders(process=process)

    def get_dispatch_lines_model(self, *, process: str = "terminaciones") -> list[Any]:
        return self._repo.get_dispatch_lines_model(process=process)

    def get_jobs_model(self, *, process: str = "terminaciones") -> list[Any]:
        return self._repo.get_jobs_model(process=process)

    def get_parts_model(self) -> list[Any]:
        return self._repo.get_parts_model()

    def build_pinned_program_seed(self, *, process: str, jobs: list[Any], parts: list[Any]):
        return self._repo.build_pinned_program_seed(process=process, jobs=jobs, parts=parts)

    def save_last_program(self, *, process: str, program: dict, errors: list[dict]) -> None:
        return self._repo.save_last_program(process=process, program=program, errors=errors)

    def get_last_program_rows(self, *, process: str = "terminaciones") -> list[dict]:
        return self._repo.get_last_program_rows(process=process)

    def set_pedido_priority(self, *, pedido: str, posicion: str, is_priority: bool) -> None:
        return self.data._repo.set_pedido_priority(pedido=pedido, posicion=posicion, is_priority=is_priority)

    def set_pedido_test(self, *, pedido: str, posicion: str, is_test: bool) -> None:
        return self.data._repo.set_pedido_test(pedido=pedido, posicion=posicion, is_test=is_test)

    def get_priority_orderpos_set(self) -> set[tuple[str, str]]:
        return self.data._repo.get_priority_orderpos_set()

    def get_test_orderpos_set(self) -> set[tuple[str, str]]:
        return self.data._repo.get_test_orderpos_set()

    def delete_all_pedido_priorities(self, *, keep_tests: bool = True) -> None:
        return self.data._repo.delete_all_pedido_priorities(keep_tests=keep_tests)

    def upsert_line(
        self,
        *,
        process: str = "terminaciones",
        line_id: int,
        line_name: str | None = None,
        families: list[str] | None = None,
        mec_perf_inclinada: bool = False,
        sobre_medida_mecanizado: bool = False,
    ) -> None:
        return self._repo.upsert_dispatch_line(
            process=process,
            line_id=line_id,
            line_name=line_name,
            families=families or [],
            mec_perf_inclinada=mec_perf_inclinada,
            sobre_medida_mecanizado=sobre_medida_mecanizado,
        )

    def delete_line(self, *, process: str = "terminaciones", line_id: int) -> None:
        return self._repo.delete_dispatch_line(process=process, line_id=line_id)

    def get_lines(self, *, process: str = "terminaciones") -> list[dict]:
        return self._repo.get_dispatch_lines_rows(process=process)

    def load_last_program(self, *, process: str = "terminaciones") -> dict | None:
        return self._repo.load_last_program(process=process)

    def mark_in_progress(
        self,
        *,
        process: str,
        pedido: str,
        posicion: str,
        is_test: int,
        line_id: int,
        split_id: int | None = None,
        qty: int | None = None,
    ) -> None:
        return self._repo.mark_in_progress(
            process=process,
            pedido=pedido,
            posicion=posicion,
            is_test=is_test,
            line_id=line_id,
            split_id=split_id,
            qty=qty,
        )

    def unmark_in_progress(
        self,
        *,
        process: str,
        pedido: str,
        posicion: str,
        is_test: int,
        split_id: int | None = None,
    ) -> None:
        return self._repo.unmark_in_progress(
            process=process,
            pedido=pedido,
            posicion=posicion,
            is_test=is_test,
            split_id=split_id,
        )

    def move_in_progress(
        self,
        *,
        process: str,
        pedido: str,
        posicion: str,
        is_test: int,
        line_id: int,
        split_id: int | None = None,
    ) -> None:
        return self._repo.move_in_progress(
            process=process,
            pedido=pedido,
            posicion=posicion,
            is_test=is_test,
            line_id=line_id,
            split_id=split_id,
        )

    def create_balanced_split(
        self,
        *,
        process: str,
        pedido: str,
        posicion: str,
        is_test: int,
        line_id: int | None = None,
        qty: int | None = None,
    ) -> int | None:
        return self._repo.create_balanced_split(
            process=process,
            pedido=pedido,
            posicion=posicion,
            is_test=is_test,
            line_id=line_id,
            qty=qty,
        )

    def split_job(self, *, job_id: str, qty_split: int) -> tuple[str, str]:
        return self._repo.split_job(job_id=job_id, qty_split=qty_split)

    def mark_job_urgent(self, job_id: str) -> None:
        return self._repo.mark_job_urgent(job_id)

    def unmark_job_urgent(self, job_id: str) -> None:
        return self._repo.unmark_job_urgent(job_id)


class PlannerRepository:
    """Planner module access: planner_* tables and schedules."""

    def __init__(self, db: Db, *, data: DataRepository) -> None:
        from foundryplan.planner.planner_repository import PlannerRepositoryImpl
        self._repo = PlannerRepositoryImpl(db=db, data_repo=data._repo)
        self.data = data

    def ensure_planner_scenario(self, *, name: str = "default") -> int:
        return self._repo.ensure_planner_scenario(name=name)

    def sync_planner_inputs_from_sap(self, *, scenario_id: int, asof_date, horizon_buffer_days: int = 10) -> dict:
        return self._repo.sync_planner_inputs_from_sap(
            scenario_id=scenario_id,
            asof_date=asof_date,
            horizon_buffer_days=horizon_buffer_days,
        )

    def get_planner_orders_rows(self, *, scenario_id: int) -> list[dict]:
        return self._repo.get_planner_orders_rows(scenario_id=scenario_id)

    def get_planner_parts_rows(self, *, scenario_id: int) -> list[dict]:
        return self._repo.get_planner_parts_rows(scenario_id=scenario_id)

    def get_planner_calendar_rows(self, *, scenario_id: int) -> list[dict]:
        return self._repo.get_planner_calendar_rows(scenario_id=scenario_id)

    def get_planner_initial_order_progress_rows(self, *, scenario_id: int, asof_date) -> list[dict]:
        return self._repo.get_planner_initial_order_progress_rows(scenario_id=scenario_id, asof_date=asof_date)

    def get_planner_initial_flask_inuse_rows(self, *, scenario_id: int, asof_date) -> list[dict]:
        return self._repo.get_planner_initial_flask_inuse_rows(scenario_id=scenario_id, asof_date=asof_date)

    def get_planner_initial_pour_load_rows(self, *, scenario_id: int, asof_date) -> list[dict]:
        return self._repo.get_planner_initial_pour_load_rows(scenario_id=scenario_id, asof_date=asof_date)

    def get_planner_initial_patterns_loaded(self, *, scenario_id: int, asof_date) -> list[dict]:
        return self._repo.get_planner_initial_patterns_loaded(scenario_id=scenario_id, asof_date=asof_date)

    def replace_planner_initial_patterns_loaded(self, *, scenario_id: int, rows: list[tuple]) -> None:
        return self._repo.replace_planner_initial_patterns_loaded(scenario_id=scenario_id, rows=rows)

    def get_planner_resources(self, *, scenario_id: int) -> dict | None:
        return self._repo.get_planner_resources(scenario_id=scenario_id)

    def upsert_planner_resources(
        self,
        *,
        scenario_id: int,
        molding_max_per_day: int,
        molding_max_same_part_per_day: int,
        pour_max_ton_per_day: float,
        notes: str | None = None,
    ) -> None:
        return self._repo.upsert_planner_resources(
            scenario_id=scenario_id,
            molding_max_per_day=molding_max_per_day,
            molding_max_same_part_per_day=molding_max_same_part_per_day,
            pour_max_ton_per_day=pour_max_ton_per_day,
            notes=notes,
        )

    def upsert_planner_flask_type(
        self,
        *,
        scenario_id: int,
        flask_type: str,
        qty_total: int,
        codes_csv: str,
        label: str,
        notes: str | None = None,
    ) -> None:
        return self._repo.upsert_planner_flask_type(
            scenario_id=scenario_id,
            flask_type=flask_type,
            qty_total=qty_total,
            codes_csv=codes_csv,
            label=label,
            notes=notes,
        )

    def delete_planner_flask_type(self, *, scenario_id: int, flask_type: str) -> None:
        return self._repo.delete_planner_flask_type(
            scenario_id=scenario_id,
            flask_type=flask_type,
        )
