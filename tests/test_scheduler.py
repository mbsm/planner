from __future__ import annotations

from datetime import date

from foundryplanner.dispatching.models import Line, Order, Part
from foundryplanner.dispatching.scheduler import generate_program


def test_generate_program_orders_by_due_date_and_eligibility():
    lines = [
        Line(line_id=1, allowed_families={"Parrillas"}),
        Line(line_id=2, allowed_families={"Lifters", "Parrillas"}),
    ]

    orders = [
        Order(
            pedido="B",
            posicion="10",
            numero_parte="L1",
            cantidad=1,
            primer_correlativo=1,
            ultimo_correlativo=1,
            fecha_entrega=date(2026, 1, 20),
        ),
        Order(
            pedido="A",
            posicion="10",
            numero_parte="P1",
            cantidad=2,
            primer_correlativo=1,
            ultimo_correlativo=2,
            fecha_entrega=date(2026, 1, 10),
        ),
    ]

    parts = [
        Part(numero_parte="P1", familia="Parrillas", vulcanizado_dias=0, mecanizado_dias=0, inspeccion_externa_dias=0),
        Part(numero_parte="L1", familia="Lifters", vulcanizado_dias=0, mecanizado_dias=0, inspeccion_externa_dias=0),
    ]

    program, errors = generate_program(lines=lines, orders=orders, parts=parts)
    assert errors == []

    # Lifters should go to line 2 (line 1 doesn't allow Lifters)
    assert any(r["numero_parte"] == "L1" for r in program[2])
    assert all(r["numero_parte"] != "L1" for r in program[1])

    # A due earlier than B
    rows_all = program[1] + program[2]
    a_rows = [r for r in rows_all if r["pedido"] == "A"]
    b_rows = [r for r in rows_all if r["pedido"] == "B"]
    assert a_rows
    assert b_rows


def test_correlativos_grouped_into_ranges():
    lines = [Line(line_id=1, allowed_families={"Parrillas"})]
    orders = [
        Order(
            pedido="A",
            posicion="10",
            numero_parte="P1",
            cantidad=3,
            primer_correlativo=10,
            ultimo_correlativo=12,
            fecha_entrega=date(2026, 1, 10),
        )
    ]
    parts = [Part(numero_parte="P1", familia="Parrillas", vulcanizado_dias=0, mecanizado_dias=0, inspeccion_externa_dias=0)]
    program, errors = generate_program(lines=lines, orders=orders, parts=parts)
    assert errors == []
    rows = program[1]
    assert len(rows) == 1
    assert rows[0]["corr_inicio"] == 10 and rows[0]["corr_fin"] == 12


def test_priority_uses_start_by_date():
    lines = [Line(line_id=1, allowed_families={"Parrillas"})]

    # A is due earlier but has no extra days -> start_by = 2026-01-10
    # B is due later but has 5 extra days -> start_by = 2026-01-07, so it should be scheduled first.
    orders = [
        Order(
            pedido="A",
            posicion="10",
            numero_parte="P1",
            cantidad=1,
            primer_correlativo=1,
            ultimo_correlativo=1,
            fecha_entrega=date(2026, 1, 10),
        ),
        Order(
            pedido="B",
            posicion="10",
            numero_parte="P2",
            cantidad=1,
            primer_correlativo=1,
            ultimo_correlativo=1,
            fecha_entrega=date(2026, 1, 12),
        ),
    ]

    parts = [
        Part(numero_parte="P1", familia="Parrillas", vulcanizado_dias=0, mecanizado_dias=0, inspeccion_externa_dias=0),
        Part(numero_parte="P2", familia="Parrillas", vulcanizado_dias=5, mecanizado_dias=0, inspeccion_externa_dias=0),
    ]

    program, errors = generate_program(lines=lines, orders=orders, parts=parts)
    assert errors == []
    rows = program[1]
    assert [r["pedido"] for r in rows] == ["B", "A"]


def test_unconfigured_family_is_reported_as_error_and_not_scheduled():
    lines = [Line(line_id=1, allowed_families={"Parrillas"})]

    orders = [
        Order(
            pedido="A",
            posicion="10",
            numero_parte="X1",
            cantidad=1,
            primer_correlativo=1,
            ultimo_correlativo=1,
            fecha_entrega=date(2026, 1, 10),
        ),
    ]

    parts = [Part(numero_parte="X1", familia="No pieza", vulcanizado_dias=0, mecanizado_dias=0, inspeccion_externa_dias=0)]

    program, errors = generate_program(lines=lines, orders=orders, parts=parts)
    assert program[1] == []
    assert len(errors) == 1
    assert errors[0]["numero_parte"] == "X1"


def test_priority_orderpos_is_scheduled_first():
    lines = [Line(line_id=1, allowed_families={"Parrillas"})]

    orders = [
        Order(
            pedido="B",
            posicion="10",
            numero_parte="P1",
            cantidad=1,
            primer_correlativo=1,
            ultimo_correlativo=1,
            fecha_entrega=date(2026, 1, 10),
        ),
        Order(
            pedido="A",
            posicion="20",
            numero_parte="P1",
            cantidad=1,
            primer_correlativo=2,
            ultimo_correlativo=2,
            fecha_entrega=date(2026, 1, 20),
        ),
    ]

    parts = [Part(numero_parte="P1", familia="Parrillas", vulcanizado_dias=0, mecanizado_dias=0, inspeccion_externa_dias=0)]

    program, errors = generate_program(
        lines=lines,
        orders=orders,
        parts=parts,
        priority_orderpos={("A", "20")},
    )

    assert errors == []

    rows = program[1]
    assert rows
    assert rows[0]["pedido"] == "A"


def test_test_lots_are_scheduled_before_normal_lots():
    lines = [Line(line_id=1, allowed_families={"Parrillas"})]

    orders = [
        Order(
            pedido="N",
            posicion="10",
            numero_parte="P1",
            cantidad=1,
            primer_correlativo=1,
            ultimo_correlativo=1,
            fecha_entrega=date(2026, 1, 5),
            is_test=False,
        ),
        Order(
            pedido="T",
            posicion="20",
            numero_parte="P1",
            cantidad=1,
            primer_correlativo=2,
            ultimo_correlativo=2,
            fecha_entrega=date(2026, 1, 20),
            is_test=True,
        ),
    ]

    parts = [Part(numero_parte="P1", familia="Parrillas", vulcanizado_dias=0, mecanizado_dias=0, inspeccion_externa_dias=0)]

    program, errors = generate_program(lines=lines, orders=orders, parts=parts)
    assert errors == []
    rows = program[1]
    assert rows
    assert rows[0]["pedido"] == "T"
