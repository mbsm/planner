from __future__ import annotations

from datetime import date, timedelta

from foundryplan.dispatcher.models import Job, Line, Part


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

        # Boolean constraint (special capability): if part REQUIRES the capability (True), 
        # line MUST have it. If part doesn't require it (False), any line works.
        # This allows lines with special capabilities to process both special AND normal parts.
        if isinstance(rule_value, bool):
            part_value = getattr(part, attr, False)
            # If part requires the capability but line doesn't have it, reject
            if part_value is True and rule_value is not True:
                return False
            # If part doesn't require capability, accept (line can process normal parts)
            continue

        # Generic attribute check on Part
        if not hasattr(part, attr):
            return False

        part_value = getattr(part, attr)

        if isinstance(rule_value, dict) and ("min" in rule_value or "max" in rule_value):
            min_v = rule_value.get("min")
            max_v = rule_value.get("max")
            if min_v is not None and part_value < min_v:
                return False
            if max_v is not None and part_value > max_v:
                return False
        elif isinstance(rule_value, (set, list, tuple)):
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
    pinned_program: dict[object, list[dict]] | None = None,
    pinned_jobs: list[Job] | None = None,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Generate dispatch queues for a specific process.

    Args:
        lines: Available resources/lines with constraints.
        jobs: Jobs to schedule (queued).
        parts: Master data for materials (lead times, attributes).
        pinned_program: Optional pre-seeded rows already fixed to a line ("en proceso").
            The scheduler will place these rows at the beginning of each line queue and
            will account for their `cantidad` as initial load when balancing remaining jobs.
        pinned_jobs: Legacy argument kept for backward compatibility (ignored).

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

    def _match_line_id(raw_key: object) -> str | None:
        """Best-effort: map an external line key (int/str) to a scheduler line_id."""
        for ln in sorted_lines:
            if str(raw_key) == str(ln.line_id):
                return ln.line_id
        try:
            raw_int = int(str(raw_key))
        except Exception:
            return None
        for ln in sorted_lines:
            try:
                if int(str(ln.line_id)) == raw_int:
                    return ln.line_id
            except Exception:
                continue
        return None

    def get_part(material: str) -> Part | None:
        return part_map.get(material)

    def calculate_start_by(job: Job) -> date:
        if job.start_by:
            return job.start_by

        if not job.fecha_de_pedido:
            return date.max  # Push to end if no date

        p = get_part(job.material)
        if not p:
            return job.fecha_de_pedido

        # Sum of lead times (vulcanizado + mecanizado + inspeccion)
        days = (p.vulcanizado_dias or 0) + (p.mecanizado_dias or 0) + (p.inspeccion_externa_dias or 0)
        return job.fecha_de_pedido - timedelta(days=days)

    # 1. Handle "Pinned" (in-progress) rows
    # The scheduler itself is pure, so it only consumes a pre-built pinned_program.
    # The caller is responsible for excluding pinned jobs from `jobs`.
    if pinned_program:
        for raw_line_key, rows in dict(pinned_program).items():
            line_id = _match_line_id(raw_line_key)
            if not line_id:
                continue
            for r in list(rows or []):
                row = dict(r)
                queues[line_id].append(row)
                try:
                    line_loads[line_id] += int(row.get("cantidad") or 0)
                except Exception:
                    pass

    # NOTE: pinned_jobs is a legacy argument (ignored).

    # 2. Process Queued Jobs
    # Sort criteria: Priority ASC (1=High), StartBy ASC

    # Augment jobs with sort keys
    augmented_jobs: list[tuple[Job, date]] = []
    for job in jobs:
        start_date = calculate_start_by(job)
        augmented_jobs.append((job, start_date))

    # Sort
    augmented_jobs.sort(key=lambda x: (x[0].priority, x[1], x[0].fecha_de_pedido or date.max))

    for job, start_date in augmented_jobs:
        part = get_part(job.material)
        if not part:
            errors.append(
                {
                    "job_id": job.job_id,
                    "error": "Material no encontrado en maestro",
                    "material": job.material,
                    "pedido": job.pedido,
                    "posicion": job.posicion,
                    "cantidad": job.qty,
                    "fecha_de_pedido": job.fecha_de_pedido.isoformat() if job.fecha_de_pedido else None,
                    "prio_kind": "test"
                    if job.is_test
                    else ("priority" if job.priority <= 2 else "normal"),
                }
            )
            continue

        # Filter valid lines
        valid_lines = [line for line in sorted_lines if check_constraints(line, part)]

        if not valid_lines:
            errors.append(
                {
                    "job_id": job.job_id,
                    "error": "Sin línea compatible (restricciones)",
                    "material": job.material,
                    "family_id": part.family_id,
                    "pedido": job.pedido,
                    "posicion": job.posicion,
                    "cantidad": job.qty,
                    "fecha_de_pedido": job.fecha_de_pedido.isoformat() if job.fecha_de_pedido else None,
                    "prio_kind": "test"
                    if job.is_test
                    else ("priority" if job.priority <= 2 else "normal"),
                }
            )
            continue

        # Assign to min-load line
        # Load balancing by quantity (piezas)
        chosen_line = min(valid_lines, key=lambda l: line_loads[l.line_id])

        # Add to queue
        row = {
            "_row_id": job.job_id,  # Use job_id as unique row identifier
            "job_id": job.job_id,
            "pedido": job.pedido,
            "posicion": job.posicion,
            "material": job.material,
            "numero_parte": job.material[-5:] if len(job.material) >= 5 else job.material,  # Truncated for UI
            "cantidad": job.qty,
            "corr_inicio": job.corr_min,  # Legacy alias for UI
            "corr_fin": job.corr_max,  # Legacy alias for UI
            "priority": job.priority,
            # Legacy prio_kind mapping for UI badge
            "prio_kind": "test" if job.is_test else ("priority" if job.priority <= 2 else "normal"),
            "is_test": job.is_test,
            "fecha_de_pedido": job.fecha_de_pedido.isoformat() if job.fecha_de_pedido else None,
            "start_by": start_date.isoformat(),
            "notes": job.notes,
            "cliente": job.cliente,
            # Constraints info only for debug?
            "family_id": part.family_id,
            "familia": part.family_id,  # Legacy alias for UI
        }

        queues[chosen_line.line_id].append(row)
        line_loads[chosen_line.line_id] += job.qty

    # Return formatted queues
    return queues, errors
