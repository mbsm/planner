# Configuración

FoundryPlanner guarda parámetros en SQLite y se administran desde **Config** dentro de la app.

En la UI:
- **Config → Dispatcher**: Centro/Almacén SAP, almacenes por proceso, líneas y familias por proceso.
- **Config → Planificador**: parámetros del solver MIP semanal + scheduler.

## Parámetros SAP

- `sap_centro`
  - Centro SAP (por defecto: `4000`).
- `sap_material_prefixes`
  - Prefijos de materiales a mantener desde MB52.
  - Ejemplo: `436`.
  - Para mantener todo: `*`.

## Almacenes por proceso

Cada proceso usa un almacén diferente (según configuración en la app). Para Terminaciones se usa:

- `sap_almacen_terminaciones` (por defecto: `4035`)

La app también expone procesos adicionales en el menú **Programas Producción** (mecanizado, inspección externa, etc.), cada uno con su propio almacén asociado.

## Reglas relevantes

- **Piezas usables (MB52)**: `libre_utilizacion=1` y `en_control_calidad=0`.
- **Pruebas (Terminaciones)**: lote alfanumérico (contiene letras) → prioridad automática.
- **Correlativo desde lote**: se toma el **prefijo numérico** (primer grupo de dígitos).

## UI

- `ui_allow_move_in_progress_line`
  - `1` habilita mover una orden marcada “en proceso” a otra línea desde el diálogo de la tabla.

## Planificador (semanal)

Parámetros del solver (CBC vía foundry_planner_engine):
- `strategy_time_limit_seconds` (default: `300`)
- `strategy_mip_gap` (default: `0.01`)
- `strategy_planning_horizon_weeks` (default: `40`)
- `strategy_solver_threads` (default: vacío = solver default)
- `strategy_solver_msg` (default: `0`)

Restricciones de planta (capacidad):
- `strategy_working_days_per_week` (default: `5`)
- `strategy_holidays` (default: vacío). Lista de fechas `YYYY-MM-DD` (una por línea o separadas por coma).
- `strategy_molds_per_day_per_line` (default: `25`)
- `strategy_pour_tons_per_day` (default: `100`)
- `strategy_working_hours_per_week` (default: `120`)
- `strategy_flasks_qty_per_line` (default: `25`)
- `strategy_flask_sizes` (default: `120,105,146`)

Scheduler del solve semanal (UTC):
- `strategy_solve_day` (0=Lunes … 6=Domingo; default: `0`)
- `strategy_solve_hour` (0-23; default: `0`)
