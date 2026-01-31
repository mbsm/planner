from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Line:
    line_id: str
    constraints: dict[str, Any]
    load_capacity: float | None = None
    
@dataclass(frozen=True)
class Job:
    job_id: str
    pedido: str
    posicion: str
    material: str
    qty: int
    priority: int
    fecha_de_pedido: date | None
    is_test: bool = False
    notes: str | None = None
    cliente: str | None = None
    
    # Scheduling info
    start_by: date | None = None
    
    # Display info (legacy/detail)
    corr_min: int | None = None
    corr_max: int | None = None
    
@dataclass(frozen=True)
class Order:
    # Deprecated v0.1 model
    pedido: str
    posicion: str
    material: str
    cantidad: int
    primer_correlativo: int
    ultimo_correlativo: int
    fecha_de_pedido: date
    tiempo_proceso_min: float | None = None
    is_test: bool = False
    cliente: str | None = None

    @property
    def numero_parte(self) -> str:
        return self.material


@dataclass(frozen=True)
class Part:
    material: str
    family_id: str
    vulcanizado_dias: int | None = None
    mecanizado_dias: int | None = None
    inspeccion_externa_dias: int | None = None
    peso_unitario_ton: float | None = None
    mec_perf_inclinada: bool = False
    sobre_medida_mecanizado: bool = False


@dataclass
class AuditEntry:
    id: int
    timestamp: str 
    category: str
    message: str
    details: str | None = None
