from __future__ import annotations

import math
from datetime import date
from typing import Any, NamedTuple

from foundryplan.planner.model import PlannerOrder, PlannerPart


# ============================================================================
# PLACEMENT RESULT
# ============================================================================

class PlacementResult(NamedTuple):
    """Resultado de intentar programar un pedido."""
    success: bool
    schedule: dict[int, int]      # day_idx -> qty_molds
    pour_days: list[int]          # días de vaciado
    release_day: int              # día final de liberación (shakeout)
    completion_day: int           # release + finishing
    finish_days_effective: int    # finish_days realmente usado (puede ser < nominal si se comprime para cumplir due_date)
    resource_deltas: dict         # cambios a aplicar
    failure_reason: str           # diagnóstico si falla


def _build_due_day_map(workdays: list[date]) -> dict[str, int]:
    return {d.isoformat(): idx for idx, d in enumerate(workdays)}


# ============================================================================
# CORE PLACEMENT FUNCTION
# ============================================================================

def try_place_order(
    *,
    order_id: str,
    part_id: str,
    qty_molds: int,
    start_day_idx: int,
    part_data: PlannerPart,
    day_state: list[dict],
    part_usage: list[dict],
    workdays: list[date],
    due_day_idx: int | None = None,
    pour_lag_days: int = 1,
    shakeout_lag_days: int = 1,
    allow_gaps: bool = False,
) -> PlacementResult:
    """
    Intenta programar una orden completa desde start_day_idx con moldeo contiguo.
    
    Validaciones críticas:
    1. Capacidad de moldeo diaria (molding_capacity)
    2. Capacidad de mismo molde por día (same_mold_capacity - usage)
    3. Disponibilidad de cajas en TODA la ventana de enfriamiento
    4. Capacidad de vaciado (toneladas) en día de colada
    
    NO modifica day_state. Retorna resource_deltas para aplicar después.
    """
    horizon = len(workdays)
    
    # Validaciones iniciales
    if start_day_idx >= horizon:
        return PlacementResult(
            False, {}, [], -1, -1, 0, {},
            f"start_day {start_day_idx} >= horizon {horizon}"
        )
    
    if qty_molds <= 0:
        return PlacementResult(True, {}, [], start_day_idx, start_day_idx, 0, {}, "")
    
    # Extraer y VALIDAR datos de pieza (NO defaults - si falta dato, FALLA)
    flask_type = str(part_data.flask_type or "").upper()
    if not flask_type:
        return PlacementResult(False, {}, [], -1, -1, 0, {}, "Dato faltante: flask_type")
    
    cool_hours = part_data.cool_hours
    if cool_hours is None or cool_hours <= 0:
        return PlacementResult(False, {}, [], -1, -1, 0, {}, f"Dato faltante o inválido: cool_hours={cool_hours}")
    cool_hours = float(cool_hours)
    
    finish_days = part_data.finish_days
    if finish_days is None or finish_days <= 0:
        return PlacementResult(False, {}, [], -1, -1, 0, {}, f"Dato faltante o inválido: finish_days={finish_days}")
    finish_days = int(finish_days)
    
    min_finish_days = part_data.min_finish_days
    if min_finish_days is None or min_finish_days <= 0:
        return PlacementResult(False, {}, [], -1, -1, 0, {}, f"Dato faltante o inválido: min_finish_days={min_finish_days}")
    min_finish_days = int(min_finish_days)
    
    pieces_per_mold = part_data.pieces_per_mold
    if pieces_per_mold is None or pieces_per_mold <= 0:
        return PlacementResult(False, {}, [], -1, -1, 0, {}, f"Dato faltante o inválido: pieces_per_mold={pieces_per_mold}")
    pieces_per_mold = float(pieces_per_mold)
    
    net_weight_ton = part_data.net_weight_ton
    if net_weight_ton is None or net_weight_ton <= 0:
        return PlacementResult(False, {}, [], -1, -1, 0, {}, f"Dato faltante o inválido: net_weight_ton={net_weight_ton}")
    net_weight_ton = float(net_weight_ton)
    
    metal_per_mold = net_weight_ton * pieces_per_mold
    cooling_days = max(1, math.ceil(cool_hours / 24.0))
    
    # Estado de construcción del plan
    schedule: dict[int, int] = {}
    pour_days_list: list[int] = []
    resource_deltas = {
        "molding": {},          # day_idx -> qty
        "same_mold": {},        # (day_idx, part_id) -> qty
        "pour": {},             # day_idx -> tons
        "flasks": {},           # (day_idx, flask_type) -> qty
    }
    
    qty_remaining = qty_molds
    current_day = start_day_idx
    last_release_day = start_day_idx
    started_molding = False
    
    # Loop principal: moldear día a día
    while qty_remaining > 0:
        # ¿Nos salimos del horizonte?
        if current_day >= horizon:
            return PlacementResult(
                False, {}, [], -1, -1, 0.0, {},
                f"Horizonte agotado en día {current_day}, faltan {qty_remaining} moldes"
            )
        
        # Calcular días críticos
        pour_day = current_day + pour_lag_days
        release_day = pour_day + cooling_days + shakeout_lag_days
        
        # ¿El release cabe en el horizonte?
        if release_day >= horizon or pour_day >= horizon:
            return PlacementResult(
                False, {}, [], -1, -1, 0.0, {},
                f"Release day {release_day} >= horizon {horizon}"
            )
        
        # ================================================================
        # CALCULAR CAPACIDAD DISPONIBLE
        # ================================================================
        
        # 1. Capacidad de moldeo general
        molding_available = (
            day_state[current_day]["molding"]
            - resource_deltas["molding"].get(current_day, 0)
        )
        
        # 2. Capacidad de mismo molde (trackear por part_id)
        same_mold_base = day_state[current_day]["same_mold"]
        same_mold_used = part_usage[current_day].get(part_id, 0)
        same_mold_planned = resource_deltas["same_mold"].get((current_day, part_id), 0)
        same_mold_available = same_mold_base - same_mold_used - same_mold_planned
        
        # 3. Capacidad de vaciado
        pour_available = (
            day_state[pour_day]["pour"]
            - resource_deltas["pour"].get(pour_day, 0)
        )
        
        # 4. CRÍTICO: Disponibilidad de cajas en TODA la ventana
        flask_window_min = float('inf')
        flask_bottleneck_day = -1
        
        for d_idx in range(current_day, release_day + 1):
            flask_at_d = (
                day_state[d_idx]["flasks"].get(flask_type, 0)
                - resource_deltas["flasks"].get((d_idx, flask_type), 0)
            )
            if flask_at_d < flask_window_min:
                flask_window_min = flask_at_d
                flask_bottleneck_day = d_idx
        
        flask_window_capacity = int(flask_window_min) if flask_window_min != float('inf') else 0
        
        # Máximo que podemos moldear HOY
        max_qty_today = min(
            qty_remaining,
            molding_available,
            same_mold_available,
            flask_window_capacity,
            int(pour_available / metal_per_mold) if metal_per_mold > 0 else 0
        )
        
        # ================================================================
        # DECISIÓN: ¿Moldeamos hoy?
        # ================================================================
        
        if max_qty_today <= 0:
            # No hay capacidad este día
            
            if started_molding and not allow_gaps:
                # Contiguidad rota - diagnosticar
                reasons = []
                if molding_available <= 0:
                    reasons.append(f"Moldeo agotado día {current_day}")
                if same_mold_available <= 0:
                    reasons.append(f"Same-mold agotado día {current_day}")
                if flask_window_capacity <= 0:
                    reasons.append(
                        f"Cajas {flask_type} agotadas en ventana [{current_day}, {release_day}] "
                        f"(bottleneck día {flask_bottleneck_day})"
                    )
                if pour_available < metal_per_mold:
                    reasons.append(f"Colada insuficiente día {pour_day} ({pour_available:.1f}t < {metal_per_mold:.1f}t)")
                
                return PlacementResult(
                    False, {}, [], -1, -1, 0.0, {},
                    f"Contiguidad rota: {'; '.join(reasons)}"
                )
            
            # No hemos empezado → avanzar
            current_day += 1
            continue
        
        # ================================================================
        # MOLDEAR max_qty_today
        # ================================================================
        
        started_molding = True
        
        # Registrar en schedule
        schedule[current_day] = max_qty_today
        pour_days_list.append(pour_day)
        
        # Registrar decrementos
        resource_deltas["molding"][current_day] = (
            resource_deltas["molding"].get(current_day, 0) + max_qty_today
        )
        
        resource_deltas["same_mold"][(current_day, part_id)] = (
            resource_deltas["same_mold"].get((current_day, part_id), 0) + max_qty_today
        )
        
        resource_deltas["pour"][pour_day] = (
            resource_deltas["pour"].get(pour_day, 0) + (max_qty_today * metal_per_mold)
        )
        
        # Ocupar cajas en TODA la ventana
        for d_idx in range(current_day, release_day + 1):
            key = (d_idx, flask_type)
            resource_deltas["flasks"][key] = (
                resource_deltas["flasks"].get(key, 0) + max_qty_today
            )
        
        # Actualizar estado
        qty_remaining -= max_qty_today
        last_release_day = max(last_release_day, release_day)
        current_day += 1
    
    # ÉXITO: Completamos toda la cantidad
    # Optimizar finish_days según due_date
    finish_days_effective = finish_days
    
    if due_day_idx is not None and due_day_idx >= 0:
        # Calcular completion con finish_days nominal
        completion_nominal = last_release_day + finish_days
        
        # Si nos pasamos del due_date, intentar comprimir hasta min_finish_days
        if completion_nominal > due_day_idx:
            # Días disponibles para finishing
            available_finish_days = max(0, due_day_idx - last_release_day)
            
            # Comprimir hasta min_finish_days
            finish_days_effective = max(min_finish_days, available_finish_days)
    
    # Calcular completion_day con finish_days_effective
    completion_day_idx = last_release_day + finish_days_effective
   
    return PlacementResult(
        success=True,
        schedule=schedule,
        pour_days=sorted(set(pour_days_list)),
        release_day=last_release_day,
        completion_day=completion_day_idx,
        finish_days_effective=finish_days_effective,
        resource_deltas=resource_deltas,
        failure_reason=""
    )


# ============================================================================
# SLIDING WINDOW SEARCH
# ============================================================================

def find_placement_for_order(
    *,
    order_id: str,
    part_id: str,
    qty_molds: int,
    part_data: PlannerPart,
    day_state: list[dict],
    part_usage: list[dict],
    workdays: list[date],
    due_day_idx: int | None = None,
    pour_lag_days: int = 1,
    shakeout_lag_days: int = 1,
    allow_gaps: bool = False,
    max_search_days: int = 365,
) -> PlacementResult:
    """
    Busca el primer día viable para programar una orden mediante sliding window.
    
    Intenta días: 0, 1, 2, ..., hasta max_search_days
    Retorna el primer placement exitoso o falla si agota intentos.
    """
    horizon = len(workdays)
    start_search_from = 0
    search_limit = min(horizon, start_search_from + max_search_days)
    
    for attempt_day in range(start_search_from, search_limit):
        placement = try_place_order(
            order_id=order_id,
            part_id=part_id,
            qty_molds=qty_molds,
            start_day_idx=attempt_day,
            part_data=part_data,
            day_state=day_state,
            part_usage=part_usage,
            workdays=workdays,
            due_day_idx=due_day_idx,
            pour_lag_days=pour_lag_days,
            shakeout_lag_days=shakeout_lag_days,
            allow_gaps=allow_gaps,
        )
        
        if placement.success:
            return placement
    
    days_searched = search_limit - start_search_from
    return PlacementResult(
        False, {}, [], -1, -1, 0.0, {},
        f"No se encontró ventana viable buscando {days_searched} días desde HOY"
    )


# ============================================================================
# APPLY PLACEMENT
# ============================================================================

def apply_placement(
    *,
    placement: PlacementResult,
    part_id: str,
    day_state: list[dict],
    part_usage: list[dict],
) -> None:
    """Aplica un placement exitoso a los estados mutables."""
    if not placement.success:
        raise ValueError("No se puede aplicar un placement fallido")
    
    deltas = placement.resource_deltas
    
    for day_idx, qty in deltas["molding"].items():
        day_state[day_idx]["molding"] -= qty
    
    for (day_idx, pid), qty in deltas["same_mold"].items():
        part_usage[day_idx][pid] = part_usage[day_idx].get(pid, 0) + qty
    
    for day_idx, tons in deltas["pour"].items():
        day_state[day_idx]["pour"] -= tons
    
    for (day_idx, flask_type), qty in deltas["flasks"].items():
        current = day_state[day_idx]["flasks"].get(flask_type, 0)
        day_state[day_idx]["flasks"][flask_type] = max(0, current - qty)


# ============================================================================
# ORDER SORTING
# ============================================================================

def sort_orders_for_planning(
    *,
    orders: list[PlannerOrder],
    parts: dict[str, PlannerPart],
) -> list[PlannerOrder]:
    """
    Ordena órdenes por: (prioridad ASC, order_id ASC)
    
    Prioridad: 1 = Urgente, 2 = Normal
    Sin concepto de "atrasadas" - solo prioridad simple.
    """
    def sort_key(order: PlannerOrder):
        priority = int(order.priority or 2)  # Default: Normal
        return (priority, str(order.order_id))
    
    return sorted(orders, key=sort_key)




# ============================================================================
# MAIN HEURISTIC FUNCTION
# ============================================================================

def solve_planner_heuristic(
    *,
    orders: list[PlannerOrder],
    parts: dict[str, PlannerPart],
    workdays: list[date],
    daily_resources: dict[int, dict[str, Any]],
    initial_patterns_loaded: set[str],  # DEPRECATED - no longer used
    max_horizon_days: int = 365,
    pour_lag_days: int = 1,
    shakeout_lag_days: int = 1,
    max_placement_search_days: int = 365,
    allow_molding_gaps: bool = False,
) -> dict:
    """
    Nueva heurística de planificación con placement sliding window.
    
    Cambios vs versión anterior:
    - Ordenamiento simple por (prioridad, order_id)
    - Sin concepto de "atrasadas" 
    - Búsqueda ASAP (siempre desde día 0)
    - Validación correcta de ventana de enfriamiento
    - Contiguidad configurable (allow_molding_gaps parámetro)
    
    Algoritmo:
    1. Ordenar órdenes por prioridad (1=Urgente, 2=Normal)
    2. Para cada orden:
       - Buscar primer día viable (sliding window 0..max_placement_search_days)  
       - Si éxito → aplicar placement
       - Si falla → registrar error
    3. Retornar schedule + métricas
    
    Args:
        max_placement_search_days: Máximo número de días hacia adelante para buscar ventanas válidas
        allow_molding_gaps: Si True, permite moldear en días no consecutivos para un mismo pedido
    """
    horizon = len(workdays)
    if horizon > max_horizon_days:
        raise ValueError(f"Horizonte de {horizon} días excede máximo de {max_horizon_days}")
    
    # Usar parámetros configurables pasados por caller
    max_search_days = max_placement_search_days
    allow_gaps = allow_molding_gaps
    
    # Construir mapa de due dates
    due_day_map = _build_due_day_map(workdays)
    
    # Convertir parts a dict si es necesario
    parts_dict = parts if isinstance(parts, dict) else {p.part_id: p for p in parts}
    
    # Inicializar estado mutable
    day_state: list[dict] = []
    part_usage: list[dict[str, int]] = []
    
    for d in range(horizon):
        res = daily_resources.get(d, {})
        day_state.append({
            "molding": int(res.get("molding_capacity", 0)),
            "same_mold": int(res.get("same_mold_capacity", 0)),
            "pour": float(res.get("pouring_tons_available", 0.0)),
            "flasks": dict(res.get("flask_available", {})),
        })
        part_usage.append({})
    
    # Filtrar órdenes sin flask capacity (igual que antes)
    available_flask_types: set[str] = set()
    for d in range(horizon):
        available_flask_types.update(daily_resources.get(d, {}).get("flask_available", {}).keys())
    
    valid_orders: list[PlannerOrder] = []
    skipped_errors: list[str] = []
    
    for o in orders:
        if o.part_id not in parts_dict:
            continue
        part = parts_dict[o.part_id]
        flask_type = str(part.flask_type or "").upper()
        
        has_capacity = any(
            daily_resources.get(d, {}).get("flask_available", {}).get(flask_type, 0) > 0
            for d in range(horizon)
        )
        
        if has_capacity:
            valid_orders.append(o)
        else:
            skipped_errors.append(
                f"Orden {o.order_id}: Flask type {flask_type} sin capacidad disponible (revisar maestro de materiales)"
            )
    
    # Ordenar órdenes por prioridad
    sorted_orders = sort_orders_for_planning(orders=valid_orders, parts=parts_dict)
    
    # Resultados
    schedule: dict[str, dict[int, int]] = {}
    pour_days_map: dict[str, list[int]] = {}
    shakeout_days_map: dict[str, int] = {}
    finish_days_used: dict[str, int] = {}
    completion_days: dict[str, int] = {}
    late_days: dict[str, int] = {}
    errors: list[str] = list(skipped_errors)
    
    # Iterar órdenes en orden de prioridad
    for order in sorted_orders:
        part = parts_dict.get(order.part_id)
        if not part:
            errors.append(f"Orden {order.order_id}: part_id {order.part_id} no encontrado")
            continue
        
        # Calcular due_day_idx para optimización de finish_hours
        due_day_idx = due_day_map.get(str(order.due_date or "").strip())
        
        # Buscar placement (ASAP desde día 0)
        placement = find_placement_for_order(
            order_id=order.order_id,
            part_id=order.part_id,
            qty_molds=order.qty,
            part_data=part,
            day_state=day_state,
            part_usage=part_usage,
            workdays=workdays,
            due_day_idx=due_day_idx,
            pour_lag_days=pour_lag_days,
            shakeout_lag_days=shakeout_lag_days,
            allow_gaps=allow_gaps,
            max_search_days=max_search_days,
        )
        
        if placement.success:
            # Aplicar cambios
            apply_placement(
                placement=placement,
                part_id=order.part_id,
                day_state=day_state,
                part_usage=part_usage,
            )
            
            # Guardar resultados
            schedule[order.order_id] = placement.schedule
            pour_days_map[order.order_id] = placement.pour_days
            shakeout_days_map[order.order_id] = placement.release_day
            completion_days[order.order_id] = placement.completion_day
            
            # Guardar finish_days efectivo (puede ser < nominal si se comprimió)
            finish_days_used[order.order_id] = placement.finish_days_effective
            
            # Calcular lateness
            due_idx = due_day_map.get(str(order.due_date or "").strip())
            if due_idx is not None:
                late = max(0, placement.completion_day - due_idx)
                late_days[order.order_id] = int(late)
            else:
                late_days[order.order_id] = 0
        else:
            # No se pudo programar
            errors.append(f"Orden {order.order_id}: {placement.failure_reason}")
    
    # Determinar status
    status = "HEURISTIC" if not errors else "HEURISTIC_INCOMPLETE"
    skipped_count = len(orders) - len(valid_orders)
    
    return {
        "status": status,
        "molds_schedule": schedule,
        "pour_days": pour_days_map,
        "shakeout_days": shakeout_days_map,
        "finish_days": finish_days_used,
        "completion_days": completion_days,
        "late_days": late_days,
        "objective": None,
        "errors": errors,
        "horizon_exceeded": len(errors) > 0,
        "skipped_orders": skipped_count,
    }
