# Quick Reference: FoundryPlanner (Updated 2026-01-25)

Resumen rápido de cómo funciona el sistema hoy (código ya implementado).

---

## Dos capas (estado actual)

### 1) Planificador semanal (estratégico)
- Motor: `foundry_planner_engine` (MIP/CBC)
- Ejecución: scheduler (UTC, configurable) o manual desde `/plano-semanal`
- Base de datos: **se ejecuta sobre `engine.db`** (separada de la DB principal)
- Objetivo: producir un plan semanal (molds por pedido/semana) respetando capacidades globales

### 2) Dispatcher (táctico, multi-proceso)
- Heurística MB52-driven, **independiente** del plan semanal hoy
- Se regenera al importar SAP (MB52 + Visión) y/o al cambiar configuración (líneas/familias/tiempos)

---

## Flujo de datos (alto nivel)

1. `/actualizar` carga Excel → guarda `sap_mb52` y `sap_vision` (DB principal)
2. Dispatcher reconstruye órdenes por proceso y genera programas (DB principal)
3. Planificador semanal hace ETL → escribe inputs a `engine.db` → corre solver → escribe outputs en `engine.db`

---

## Rutas importantes

- `/` Dashboard (KPI Visión + métricas)
- `/actualizar` carga SAP (MB52 + Visión)
- `/programa` colas por proceso/línea (dispatcher)
- `/plano-semanal` vista estratégica + “Forzar planificación”
- `/config` (Dispatcher) almacenes por proceso + líneas/familias (colapsable, badges)
- `/config/planificador` parámetros del solver y scheduler

---

## Documentos útiles

- Arquitectura: [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md)
- Checklist actualizado: [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)
- Configuración: [docs/configuracion.md](docs/configuracion.md)
- Solver: [docs/solver_configuration.md](docs/solver_configuration.md)

---

## Config keys (planificador)

- `strategy_time_limit_seconds`, `strategy_mip_gap`, `strategy_planning_horizon_weeks`
- `strategy_solver_threads` (opcional), `strategy_solver_msg`
- Scheduler (UTC): `strategy_solve_day` (0-6), `strategy_solve_hour` (0-23)

---

## Comandos

```bash
git submodule update --init --recursive
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_app.py --port 8080
.venv/bin/python -m pytest

# Run tests
.venv/bin/python -m pytest

# Manual solve trigger (for testing)
curl -X POST http://localhost:8080/api/solve-weekly-plan
```

---

## Key Contacts & Resources

- **Foundry Planning Engine:** https://github.com/mbsm/foundry_planner_engine
- **Architecture design:** [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md)
- **Implementation tasks:** [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)
- **AI agent guide:** [.github/copilot-instructions.md](.github/copilot-instructions.md)

---

## Timeline at a Glance

```
Week 1-2: Foundation (rename, schema, foundry_planner_engine dep)
Week 2-3: ETL & Data Adapter (StrategyDataBridge, ResultReader)
Week 3-4: Orchestration (StrategyOrchestrator, enhanced scheduler)
Week 4-5: UI & Docs (/plano-semanal page, docs)
Week 5-6: Testing & Validation (unit + integration tests)
Week 6-7: Deploy & Monitor (production release, SLA)
Month 2+: Harvest benefits (monitor lateness, line smoothness)
```

---

## Need More Detail?

- **"How does the MIP solver work?"** → See foundry_planner_engine README + INTEGRATION_ARCHITECTURE.md
- **"What code do I need to write?"** → See IMPLEMENTATION_CHECKLIST.md
- **"What data goes where?"** → See INTEGRATION_ARCHITECTURE.md (data architecture section)
- **"How should AI agents understand this?"** → See [.github/copilot-instructions.md](.github/copilot-instructions.md)
