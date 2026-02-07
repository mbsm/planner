from __future__ import annotations

import logging

from foundryplan.data.db import Db
from foundryplan.data.repository_views import DataRepository, DispatcherRepository, PlannerRepository


logger = logging.getLogger(__name__)


class Repository:
    """Public repository facade.
    
    Exposes three module-specific views:
    - repo.data: SAP snapshots, config, material master, orders
    - repo.dispatcher: Jobs, dispatch lines, in-progress locks, programs
    - repo.planner: Planner scenarios, orders/parts/resources, calendar
    
    Each view instantiates its own implementation class from the respective module:
    - DataRepository â†’ DataRepositoryImpl (data_repository.py)
    - DispatcherRepository â†’ DispatcherRepositoryImpl (dispatcher_repository.py)
    - PlannerRepository â†’ PlannerRepositoryImpl (planner_repository.py)
    """

    def __init__(self, db: Db):
        self.db = db
        self.data = DataRepository(db)
        self.dispatcher = DispatcherRepository(db, data=self.data)
        self.planner = PlannerRepository(db, data=self.data)
    # Backward compatibility shortcuts - delegate to appropriate views
    def set_config(self, *, key: str, value: str) -> None:
        return self.data.set_config(key=key, value=value)
    
    def rebuild_orders_from_sap_for(self, *, process: str = "terminaciones") -> int:
        return self.data.rebuild_orders_from_sap_for(process=process)
    
    def upsert_part_master(self, **kwargs) -> None:
        return self.data.upsert_part_master(**kwargs)
    
    def move_in_progress(self, *, process: str = "terminaciones", pedido: str, posicion: str, is_test: int = 0, line_id: int, split_id: int | None = None) -> None:
        return self.dispatcher.move_in_progress(process=process, pedido=pedido, posicion=posicion, is_test=is_test, line_id=line_id, split_id=split_id)
    
    def get_orders_overdue_rows(self, *, today=None, limit: int = 200) -> list[dict]:
        return self.data.get_orders_overdue_rows(today=today, limit=limit)
    
    def get_orders_due_soon_rows(self, *, today=None, limit: int = 200, days: int = 7) -> list[dict]:
        return self.data.get_orders_due_soon_rows(today=today, limit=limit, days=days)
    
    def import_sap_mb52_bytes(self, *, content: bytes, mode: str = "replace") -> None:
        return self.data.import_sap_mb52_bytes(content=content, mode=mode)
    
    def import_sap_vision_bytes(self, *, content: bytes) -> None:
        return self.data.import_sap_vision_bytes(content=content)
    
    def import_excel_bytes(self, *, content: bytes, kind: str) -> int:
        kind_s = str(kind or "").lower()
        if kind_s == "mb52":
            self.import_sap_mb52_bytes(content=content, mode="replace")
            return 1
        if kind_s == "vision":
            self.import_sap_vision_bytes(content=content)
            return 1
        if kind_s == "demolding":
            return self.import_sap_demolding_bytes(content=content)
        return self.data.import_excel_bytes(content=content, kind=kind)
    
    def import_sap_demolding_bytes(self, *, content: bytes) -> int:
        return self.data.import_sap_demolding_bytes(content=content)
    
    def upsert_vision_kpi_daily(self, **kwargs) -> None:
        return self.data.upsert_vision_kpi_daily(**kwargs)
    
    def mark_job_urgent(self, job_id: str) -> None:
        return self.dispatcher.mark_job_urgent(job_id)
    
    def unmark_job_urgent(self, job_id: str) -> None:
        return self.dispatcher.unmark_job_urgent(job_id)
    
    def delete_all_pedido_priorities(self, *, keep_tests: bool = True) -> None:
        return self.dispatcher.delete_all_pedido_priorities(keep_tests=keep_tests)

    def split_job(self, *, job_id: str, qty_split: int) -> tuple[str, str]:
        return self.dispatcher.split_job(job_id=job_id, qty_split=qty_split)
