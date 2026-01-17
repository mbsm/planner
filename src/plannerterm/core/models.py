from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Line:
    line_id: int
    allowed_families: set[str]

@dataclass(frozen=True)
class Order:
    pedido: str
    posicion: str
    numero_parte: str
    cantidad: int
    primer_correlativo: int
    ultimo_correlativo: int
    fecha_entrega: date
    tiempo_proceso_min: float | None = None
    is_test: bool = False


@dataclass(frozen=True)
class Part:
    numero_parte: str
    familia: str
    vulcanizado_dias: int | None = None
    mecanizado_dias: int | None = None
    inspeccion_externa_dias: int | None = None
