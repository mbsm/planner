from __future__ import annotations

from datetime import date, timedelta

from plannerterm.core.models import Line, Order, Part


def generate_program(
    *,
    lines: list[Line],
    orders: list[Order],
    parts: list[Part],
    priority_orderpos: set[tuple[str, str]] | None = None,
) -> tuple[dict[int, list[dict]], list[dict]]:
    """Generate per-line queues.

    Heurística v1:
    - Ordena pedidos por fecha_entrega ascendente.
    - Cada pedido corresponde a 1 número de parte con un rango continuo de correlativos.
    - Asigna cada pedido a la línea elegible con menor carga actual.
    - Produce salida por línea con: parte, cantidad, rango de correlativos.

    Returns: dict[line_id] -> list[rows]
    """

    part_by_num = {p.numero_parte: p for p in parts}

    # lines sorted by id for deterministic output
    lines_sorted = sorted(lines, key=lambda line: line.line_id)
    loads: dict[int, int] = {line.line_id: 0 for line in lines_sorted}
    out: dict[int, list[dict]] = {line.line_id: [] for line in lines_sorted}
    errors: list[dict] = []

    def extra_days(numero_parte: str) -> int:
        p = part_by_num.get(numero_parte)
        if p is None:
            return 0
        return int(p.vulcanizado_dias or 0) + int(p.mecanizado_dias or 0) + int(p.inspeccion_externa_dias or 0)

    def start_by(o: Order) -> date:
        # Priority metric: fecha_entrega - post-process days
        return o.fecha_entrega - timedelta(days=extra_days(o.numero_parte))

    priority_set = set(priority_orderpos or set())

    def prio_rank(o: Order) -> int:
        if bool(getattr(o, "is_test", False)):
            return 2
        if (o.pedido, o.posicion) in priority_set:
            return 1
        return 0

    def prio_kind_label(o: Order) -> str:
        if bool(getattr(o, "is_test", False)):
            return "test"
        if (o.pedido, o.posicion) in priority_set:
            return "priority"
        return "normal"

    def sort_key(o: Order):
        # Tests first, then manual priority, then regular ordering.
        return (-prio_rank(o), start_by(o), o.fecha_entrega, o.pedido, o.posicion)

    for o in sorted(orders, key=sort_key):
        numero_parte = o.numero_parte
        familia = (part_by_num.get(numero_parte).familia if numero_parte in part_by_num else "Otros")

        eligible = [line for line in lines_sorted if familia in line.allowed_families]
        if not eligible:
            errors.append(
                {
                    "_row_id": f"ERR|{o.pedido}|{o.posicion}|{o.numero_parte}",
                    "pedido": o.pedido,
                    "posicion": o.posicion,
                    "numero_parte": o.numero_parte,
                    "familia": familia,
                    "cantidad": int(o.cantidad),
                    "fecha_entrega": o.fecha_entrega.isoformat(),
                    "start_by": start_by(o).isoformat(),
                    "prio_kind": prio_kind_label(o),
                    "error": "Familia no configurada en ninguna línea",
                }
            )
            continue

        chosen = min(eligible, key=lambda line: loads[line.line_id])

        out[chosen.line_id].append(
            {
                "_row_id": f"{o.pedido}|{o.posicion}|{o.numero_parte}|{o.primer_correlativo}-{o.ultimo_correlativo}",
                "prio_kind": prio_kind_label(o),
                "pedido": o.pedido,
                "posicion": o.posicion,
                "numero_parte": o.numero_parte,
                "cantidad": int(o.cantidad),
                "corr_inicio": int(o.primer_correlativo),
                "corr_fin": int(o.ultimo_correlativo),
                "familia": familia,
                "fecha_entrega": o.fecha_entrega.isoformat(),
                "start_by": start_by(o).isoformat(),
            }
        )
        loads[chosen.line_id] += int(o.cantidad)

    return out, errors
