import pytest
from datetime import date
from foundryplan.core.models import Job, Part, Line
from foundryplan.core.scheduler import generate_dispatch_program, check_constraints

def test_check_constraints():
    l1 = Line(line_id="L1", constraints={"family_id": {"A", "B"}})
    l2 = Line(line_id="L2", constraints={"family_id": "C"})
    l3 = Line(line_id="L3", constraints={"mec_perf_inclinada": True})
    
    pA = Part(material="M1", family_id="A")
    pC = Part(material="M2", family_id="C")
    pSpecial = Part(material="M3", family_id="A", mec_perf_inclinada=True)
    pNotSpecial = Part(material="M4", family_id="A", mec_perf_inclinada=False)
    
    assert check_constraints(l1, pA) is True
    assert check_constraints(l1, pC) is False
    assert check_constraints(l2, pC) is True
    assert check_constraints(l3, pSpecial) is True
    assert check_constraints(l3, pNotSpecial) is False

def test_scheduler_balancing_and_start_by():
    # Setup
    lines = [
        Line(line_id="L1", constraints={"family_id": {"A"}}),
        Line(line_id="L2", constraints={"family_id": {"A"}})
    ]
    parts = [
        Part(material="M1", family_id="A", vulcanizado_dias=2, mecanizado_dias=3, inspeccion_externa_dias=0)
    ]
    
    # 3 Jobs. 
    # J1: Priority 1 (High). Delivery in 10 days. Start by = 10 - 5 = 5.
    # J2: Priority 3 (Normal). Delivery in 8 days. Start by = 8 - 5 = 3.
    # J3: Priority 3 (Normal). Delivery in 20 days. Start by = 20 - 5 = 15.
    
    j1 = Job(job_id="J1", pedido="P1", posicion="1", material="M1", qty=100, priority=1, fecha_de_pedido=date(2023, 1, 10))
    j2 = Job(job_id="J2", pedido="P2", posicion="1", material="M1", qty=50, priority=3, fecha_de_pedido=date(2023, 1, 8))
    j3 = Job(job_id="J3", pedido="P3", posicion="1", material="M1", qty=50, priority=3, fecha_de_pedido=date(2023, 1, 20))
    
    jobs = [j1, j2, j3]
    
    # Run
    queues, errors = generate_dispatch_program(lines=lines, jobs=jobs, parts=parts)
    
    assert not errors
    
    # Logic verification:
    # Sorted Order: 
    # 1. J1 (Prio 1) -> Assigned to L1 (Load 0 -> 100).
    # 2. J2 (Prio 3, StartBy 3 Jan). -> Assigned to L2 (Load 0 -> 50).
    # 3. J3 (Prio 3, StartBy 15 Jan). -> Assigned to L2 (Load 50 vs L1 100 -> L2).
    
    # Verify Assignments
    q1 = queues["L1"]
    q2 = queues["L2"]
    
    assert len(q1) == 1
    assert q1[0]["job_id"] == "J1"
    
    assert len(q2) == 2
    # Check order in L2?
    # Expected: J2, then J3?
    # Yes, J2 StartBy=3, J3 StartBy=15. Sort key includes StartBy.
    assert q2[0]["job_id"] == "J2"
    assert q2[1]["job_id"] == "J3"
    
    # Verify Legacy Fields
    row = q1[0]
    assert row["numero_parte"] == "M1"
    assert row["prio_kind"] == "priority" # Prio 1
    
    row2 = q2[0]
    assert row2["prio_kind"] == "normal" # Prio 3
