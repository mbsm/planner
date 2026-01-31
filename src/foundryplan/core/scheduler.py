from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from foundryplan.core.models import Line, Job, Part

def check_constraints(line: Line, part: Part) -> bool:
    """Check if a part satisfies all constraints of a line."""
    for attr, rule_value in line.constraints.items():
        # strict attribute matching for now
        # Special case: family_id matches if in set/list
        if attr == "family_id":
            if isinstance(rule_value, (set, list, tuple)):
                if part.family_id not in rule_value:
                    return False
            elif rule_value != part.family_id:
                return False
            continue
            
        # Generic attribute check on Part
        if not hasattr(part, attr):
            # If line has a constraint on an attribute the part doesn't have (or isn't in Part model),
            # we assume the part can't satisfy it (safe fail) or we ignore?
            # For this project, Part model fields are strict.
            # If the attribute is missing on the Part object, maybe it's dynamic?
            # Let's assume strict attribute existence for defined fields.
            continue
            
        part_value = getattr(part, attr)
        
        # Boolean constraints (e.g. mec_perf_inclinada=True means line accepts ONLY if True? 
        # OR line CAPABLE of perforacion? 
        # Usually constraints are: "Line requires X" or "Line accepts only Y".
        # Let's assume the DB `resource_constraint` holds "Allowed Values".
        
        if isinstance(rule_value, (set, list, tuple)):
             if part_value not in rule_value:
                 return False
        elif rule_value != part_value:
             return False

    return True

def generate_dispatch_program(
    *,
    lines: list[Line],
    jobs: list[Job],
    parts: list[Part],
    pinned_jobs: list[Job] | None = None
) -> tuple[dict[str, list[dict]], list[dict]]:
    """
    Generate dispatch queues for a specific process.
    
    Args:
        lines: Available resources/lines with constraints.
        jobs: Jobs to schedule (queued).
        parts: Master data for materials (lead times, attributes).
        pinned_jobs: Jobs that are already 'in_process' and fixed to a line.
        
    Returns:
        (queues, errors):
            queues: dict mapping line_id -> list of job dicts (ordered)
            errors: list of jobs that could not be scheduled
    """
    
    # Index parts for quick lookup
    part_map = {p.material: p for p in parts}
    
    # Initialize output structures
    # lines sorted by ID for deterministic behavior
    sorted_lines = sorted(lines, key=lambda x: x.line_id)
    queues: dict[str, list[dict]] = {line.line_id: [] for line in sorted_lines}
    line_loads: dict[str, int] = {line.line_id: 0 for line in sorted_lines}
    errors: list[dict] = []
    
    def get_part(material: str) -> Part | None:
        return part_map.get(material)
        
    def calculate_start_by(job: Job) -> date:
        if job.start_by:
            return job.start_by
            
        if not job.fecha_entrega:
             return date.max # Push to end if no date
             
        p = get_part(job.material)
        if not p:
            return job.fecha_entrega
            
        # Sum of lead times (vulcanizado + mecanizado + inspeccion)
        days = (p.vulcanizado_dias or 0) + (p.mecanizado_dias or 0) + (p.inspeccion_externa_dias or 0)
        return job.fecha_entrega - timedelta(days=days)

    # 1. Handle Pinned Jobs
    # Note: pinned_jobs handling is usually done by merging 'frozen' items from previous program.
    # If the caller passes them here, we assume they are already assigned, 
    # but the Job model doesn't carry 'line_id'.
    # For now, we ignore 'pinned_jobs' argument in calculations unless we decide on a structure.
    # To act as a pure function, we assume 'jobs' contains EVERYTHING to be scheduled freely.
    # If pinned logic is needed, caller should pre-fill queues or we need a new argument struct.
    # We will proceed with scheduling 'jobs' only.

    # 2. Process Queued Jobs
    # Sort criteria: Priority ASC (1=High), StartBy ASC
    
    # Augment jobs with sort keys
    augmented_jobs = []
    for job in jobs:
        start_date = calculate_start_by(job)
        augmented_jobs.append((job, start_date))
        
    # Sort
    augmented_jobs.sort(key=lambda x: (x[0].priority, x[1], x[0].fecha_entrega or date.max))
    
    for job, start_date in augmented_jobs:
        part = get_part(job.material)
        if not part:
            errors.append({
                "job_id": job.job_id,
                "error": "Material no encontrado en maestro",
                "material": job.material,
                "pedido": job.pedido,
                "posicion": job.posicion,
                "cantidad": job.qty_total,
                "fecha_entrega": job.fecha_entrega.isoformat() if job.fecha_entrega else None,
                "prio_kind": "test" if job.is_test else ("priority" if job.priority <= 2 else "normal"),
            })
            continue
            
        # Filter valid lines
        valid_lines = [
            line for line in sorted_lines 
            if check_constraints(line, part)
        ]
        
        if not valid_lines:
            errors.append({
                "job_id": job.job_id,
                "error": "Sin línea compatible (restricciones)",
                "material": job.material,
                "family_id": part.family_id,
                "pedido": job.pedido,
                "posicion": job.posicion,
                "cantidad": job.qty_total,
                "fecha_entrega": job.fecha_entrega.isoformat() if job.fecha_entrega else None,
                "prio_kind": "test" if job.is_test else ("priority" if job.priority <= 2 else "normal"),
            })
            continue
            
        # Assign to min-load line
        # Load balancing by quantity (piezas)
        chosen_line = min(valid_lines, key=lambda l: line_loads[l.line_id])
        
        # Add to queue
        row = {
            "_row_id": job.job_id, # Use job_id as unique row identifier
            "job_id": job.job_id,
            "pedido": job.pedido,
            "posicion": job.posicion,
            "material": job.material,
            "numero_parte": job.material, # Legacy alias for UI
            "cantidad": job.qty_total,
            "corr_inicio": job.corr_min, # Legacy alias for UI
            "corr_fin": job.corr_max, # Legacy alias for UI
            "priority": job.priority,
            # Legacy prio_kind mapping for UI badge
            "prio_kind": "test" if job.is_test else ("priority" if job.priority <= 2 else "normal"),
            "is_test": job.is_test,
            "fecha_entrega": job.fecha_entrega.isoformat() if job.fecha_entrega else None,
            "start_by": start_date.isoformat(),
            "notes": job.notes,
            # Constraints info only for debug?
            "family_id": part.family_id,
            "familia": part.family_id # Legacy alias for UI
        }
        
        queues[chosen_line.line_id].append(row)
        line_loads[chosen_line.line_id] += job.qty_total
        
    # Return formatted queues
    return queues, errors
