# Foundry Plan — Manual de Desarrollo y Especificación Técnica

Este documento consolida la arquitectura, especificación, modelo de datos y detalles de implementación de Foundry Plan.

---

## 1. Visión Técnica

Foundry Plan es una aplicación web (NiceGUI) con backend Python y persistencia en SQLite para la planificación y despacho de producción en fundiciones "Make-to-Order".

### 1.1 Arquitectura
El sistema sigue una arquitectura modular en torno a un núcleo funcional:
- **UI (Frontend/Backend)**: `src/foundryplan/ui/` (NiceGUI). Renderizado servidor.
- **Dispatcher**: `src/foundryplan/dispatcher/` (Scheduler heurístico por proceso/recursos, genera colas ejecutables).
- **Planner Module**: `src/foundryplan/planner/` (Scheduler optimizado con OR-Tools).
- **Data Access**: `src/foundryplan/data/` (Repositorio, DB Schema, Excel I/O).
- **Persistencia**: SQLite local (`foundryplan.db`).

### 1.2 Tecnologías
- **Lenguaje**: Python 3.11+.
- **UI Framework**: NiceGUI (basado en FastAPI/Vue).
- **Base de Datos**: SQLite (con modo WAL estricto).
- **Solver**: Google OR-Tools (CP-SAT) para el módulo Planner.
- **Testing**: Pytest.

---

## 2. Modelo de Datos (Data Model)

El modelo combina datos transaccionales importados (SAP) con datos maestros locales.

**Referencia Detallada:** Para el esquema completo, lista de columnas de bases de datos y mapeo exacto de Excel, consultar el documento [modelo-datos.md](modelo-datos.md).

### 2.1 Fuentes Externas (SAP)
La aplicación ingiere archivos Excel crudos. La estrategia es "Snapshot de reemplazo total": cada carga reemplaza el estado anterior.

#### A. MB52 (Stock)
Representa stock físico por lote.
- **Tabla DB**: `sap_mb52_snapshot`
- **Mapeo Clave**:
    - `material` (Número de parte)
    - `centro`, `almacen` (Ubicación)
    - `lote` (Identificador único físico, usado para trazabilidad)
    - `documento_comercial`, `posicion_sd` (Enlace a pedido)
- **Reglas**:
    - Se importan todos los registros pertinentes al centro/almacén.
    - El filtrado por estado (`libre_utilizacion`, `en_control_calidad`) se aplica dinámicamente según reglas de proceso (ver 2.2 Configuración).
    - Lotes alfanuméricos se marcan como `is_test=1`.

#### B. Visión Planta (Demand)
Representa la cartera de pedidos y fechas.
- **Tabla DB**: `sap_vision_snapshot`
- **Mapeo Clave**:
    - `pedido`, `posicion` (PK compuesta de la demanda)
    - `fecha_de_pedido` (Fecha comprometida con cliente, driver principal del plan)
    - `solicitado` (Cantidad original)
    - `peso_neto_ton` (Peso total del pedido) => Usado para calcular peso unitario.

#### C. Reporte Desmoldeo (WIP Enfriamiento) - *Por Implementar*
Fuente SAP que informa qué moldes están actualmente en proceso de enfriamiento y cuándo se liberarán las cajas.
- **Tabla DB**: `sap_demolding_snapshot` (Propuesta)
- **Mapeo Clave**:
    - `material` <= `Pieza`
    - `texto_breve` <= `Tipo pieza`
    - `lote` <= `Lote`
    - `flask_id` <= `Caja` (ID de la caja)
    - `demolding_date` <= `Fecha Desmoldeo` (Dato real a usar)
    - `demolding_time` <= `Hora Desm.`
    - `mold_type` <= `Tipo molde` (Identifica pruebas/muestras)
    - `poured_date` <= `Fecha fundida`
    - `poured_time` <= `Hora Fundida`
    - `cooling_hours` <= `Hs. Enfria`
    - `mold_quantity` <= `Cant. Moldes` (0 a 1)
- **Campos a guardar (Futuro)**: `pieces_per_tray` (Piezas x bandeja), `manufacturing_order` (OF), `tt_curve_high` (Curva TT Alta), `tt_curve_low` (Curva TT Baja), `yard_location` (Cancha).
- **Campos a ignorar**: `Enfriamiento`, `Fecha a desmoldear` (estimado), `Colada`, `UA de Molde`, `Días para entregar`.

### 2.2 Datos Maestros Locales
Datos necesarios para la planificación que no existen o no son fiables en SAP.

#### A. Maestro de Materiales (`material_master`)
Tabla local editada por el usuario.
- **Campos Clave**:
    - `material` (PK)
    - `family_id` (FK a `family_catalog`): Determina ruta de proceso.
    - `peso_unitario_ton` (Net Weight): Copiado/derivado de Visión, editable.
    - **Tiempos (días)**: `vulcanizado_dias`, `mecanizado_dias`, `inspeccion_externa_dias`.
    - **Atributos Moldeo**: `flask_size` (S/M/L), `piezas_por_molde`, `tiempo_enfriamiento_molde_dias`, `aleacion`.
    - **Restricciones**: `mec_perf_inclinada`, `sobre_medida_mecanizado`.

#### B. Configuración de Planta
- `family_catalog`: Familias de productos.
- `process`, `resource`: Definición de líneas productivas y sus capacidades.
- `resource_constraint`: Reglas que vinculan atributos de `material_master` con `resource` (ej: Línea X solo acepta Familia Y).
- **Reglas de Stock por Proceso** (*Por Implementar*): Definición configurable de qué lotes se consideran disponibles para cada proceso.
    - Ej: *Terminaciones* requiere `almacen=X AND libre_utilizacion=1 AND en_control_calidad=0`.
    - Ej: *Toma de Dureza* requiere `almacen=X AND en_control_calidad=1`.

---

## 3. Módulos del Sistema

### 3.1 Dispatcher
Responsable de generar la **secuencia de procesamiento** (colas de trabajo / dispatch) por **proceso** y por **línea/recurso** en la planta.

El Dispatcher considera reglas de negocio para **pruebas** y **urgencias de cliente**, intentando producir en el mejor orden para cumplir eficientemente las fechas de pedido.

Un punto clave: el Dispatcher se alimenta de **información real de ejecución** (stock real por proceso y bloqueos “en proceso”), no de un “programa ideal”. Por diseño, su salida es **siempre ejecutable**: es decir, solo programa lo que efectivamente está disponible para ser procesado en ese proceso.

- **Ubicación (algoritmo puro)**: `src/foundryplan/dispatcher/scheduler.py`
- **Ubicación (armado de inputs + persistencia)**: `src/foundryplan/data/repository.py`

#### 3.1.1 Universo de trabajo desde stock (MB52 → Job/JobUnit)
El sistema construye el universo de trabajo *a partir del stock disponible del proceso* (MB52), no desde la demanda.

- **Momento de construcción**: al importar MB52, `Repository.import_sap_mb52_bytes()` ejecuta `Repository._create_jobs_from_mb52()`.
- **Filtro por proceso**:
    - Para cada proceso activo (`process.is_active=1`) se toma su `process.sap_almacen`.
    - Se filtra `sap_mb52_snapshot` por `centro` (config `sap_centro`), `almacen = process.sap_almacen` y un predicado de disponibilidad (`process.availability_predicate_json`).
    - Esto permite que cada proceso tenga su propia regla (ej.: Terminaciones vs Toma de Dureza).
- **Job (cabecera)**: el **job es la unidad de trabajo que el Dispatcher despacha**.
    - Representa un **conjunto de lotes** pertenecientes a un **pedido/posición** para un material, dentro de un proceso.
    - Se crea/actualiza **1 job por (process_id, pedido, posición, material, is_test)**.
    - `job.qty` es el **número de lotes** disponibles en el stock del proceso para ese bucket.
    - `job.is_test` viene desde MB52 (`sap_mb52_snapshot.is_test`), derivado del lote:
        - Lote alfanumérico ⇒ `is_test=1` (prueba)
        - Lote numérico ⇒ `is_test=0` (normal)
    - **Auto-split (prueba vs normal)**: si para el mismo (pedido/posición/material) existen lotes de prueba y lotes normales, el sistema crea **dos jobs separados**. Esto evita que un único lote de prueba “contamine” la prioridad/semántica del resto.
- **JobUnit (detalle por lote)**: se crea **1 job_unit por lote** (`job_unit.lote`) con `qty=1`.
    - `job_unit.correlativo_int` se deriva desde el lote para orden/visualización.

#### 3.1.2 Splits y retención de lotes
Un job representa un conjunto de lotes; el sistema soporta división (split) para poder despachar en paralelo.

- **Split a nivel de lotes (DB)**: `Repository.split_job(job_id, qty_split)` divide un job en dos jobs.
    - El split mueve lotes reales (`job_unit`) al nuevo job.
    - La UI puede implementar un split “balanceado” usando `qty_split = floor(qty/2)`.
- **Retención y reconciliación con MB52**: al reimportar MB52:
    - Si un lote ya existía, se mantiene asignado a su job actual (se preserva el split).
    - Si aparece un lote nuevo para el mismo pedido/posición/material, se asigna al job con menor `qty` (el “más vacío”).
    - Si un lote desaparece del MB52 del proceso, se elimina del `job_unit` correspondiente.
    - Si un job queda con `qty=0`, se elimina (principio “SAP es fuente de verdad”: sin stock, no hay job).

Nota: existe además un split de UI para filas “en proceso” (`Repository.create_balanced_split`) que divide una fila del **programa** (pinned) en partes `split_id=1/2` para balancear cantidad/rango; este split es a nivel de **programa** y no reasigna `job_unit`.

#### 3.1.3 Fechas y prioridad
- **Fecha comprometida (`fecha_de_pedido`)**: se actualiza desde Visión Planta (`sap_vision_snapshot`) hacia `job.fecha_de_pedido`.
- **`start_by`** (fecha sugerida de inicio): el scheduler calcula
    - `start_by = fecha_de_pedido - (vulcanizado_dias + mecanizado_dias + inspeccion_externa_dias)`
    - Los tiempos vienen del maestro `material_master`.
- **Prioridad (`job.priority`)**: número entero donde menor = más prioritario.
    - Se calcula desde configuración `job_priority_map` (por defecto: prueba=1, urgente=2, normal=3).
    - “Urgente” proviene de marcas de usuario (`orderpos_priority`, excluyendo el tipo `test`).

#### 3.1.4 Scheduling a colas por línea
Con el universo de jobs listo, el scheduler genera colas por línea. La **unidad mínima que se asigna a una línea es el job** (un conjunto de lotes por pedido/posición):

- **Orden de procesamiento**: ordena jobs por `(priority ASC, start_by ASC, fecha_de_pedido ASC)`.
- **Factibilidad**: para cada job toma su `Part` (maestro) y filtra líneas factibles con `check_constraints` (hoy principalmente `family_id`; otros atributos están soportados por la función).
- **Balanceo de líneas**: asigna cada job a la línea factible con **menor carga acumulada** (la carga se aproxima con la suma de `job.qty`).

#### 3.1.5 Fijar trabajos “en proceso” (no mover de línea)
Los trabajos marcados “en proceso” por el usuario se fijan a una línea específica y se usan como **carga inicial** para balancear el resto:

- Se registran en `program_in_progress_item` (incluye `line_id`, `marked_at` y opcionalmente `split_id`).
- Antes de ejecutar el scheduler, `Repository.build_pinned_program_seed()`:
    - Construye `pinned_program` (filas “en proceso” por línea, incluyendo splits `split_id`).
    - Remueve del universo a programar los jobs que estén bloqueados (“en proceso”) para evitar duplicados.
- El scheduler (`generate_dispatch_program`) pre-carga cada cola con `pinned_program` y suma su `cantidad` a la carga de línea antes de asignar jobs restantes.
- Al guardar/cargar el programa, `Repository._apply_in_progress_locks()` sigue reconciliando best-effort:
    - Remueve filas bloqueadas existentes y vuelve a anteponer las filas “en proceso” según DB (ordenadas por `marked_at`).
    - Ajusta cantidades/rangos desde la verdad actual (`orders`) y elimina locks inválidos si el pedido ya no existe.

#### 3.1.6 Output del Dispatcher y visualización en la UI
El algoritmo puro (`generate_dispatch_program`) genera dos salidas:

- **Programa**: `program` es un diccionario `line_id -> lista[filas]`, donde cada fila representa un job planificado (en orden) para esa línea.
    - Cada fila contiene campos como: `job_id`, `pedido`, `posicion`, `material`, `cantidad`, `priority`, `prio_kind`, `fecha_de_pedido`, `start_by`, `corr_inicio`, `corr_fin`.
- **Errores / No programadas**: `errors` es una lista de filas que no pudieron asignarse (por ejemplo, material sin maestro o sin línea compatible).

Persistencia y vista:

- La UI invoca el Dispatcher por proceso y persiste el resultado en la tabla `last_program` (JSON del programa + lista de errores) mediante `Repository.save_last_program(process, program, errors)`.
- En las páginas de “Programas Producción”, el usuario ve:
    - Una pestaña **Programa** con tablas por línea (una tarjeta por línea) mostrando el orden de ejecución.
    - Una pestaña **No programadas** (si aplica) con un conteo y un detalle de los motivos.
    - El timestamp de “Última actualización”.

Nota: los ítems marcados “en proceso” se muestran fijados en su línea y al inicio de la cola, y el resto de los jobs se ordena/redistribuye bajo las reglas del Dispatcher.

### 3.2 Planner (Nuevo)
Responsable de la planificación de *Moldeo* (nivel orden, semanal).
- **Ubicación**: `src/foundryplan/planner/`
- **Objetivo**: Decidir cuántos moldes producir por día por pedido.
- **Entradas**:
    - `PlannerOrder`: Pedidos pendientes (Visión).
    - `PlannerPart`: Atributos de moldeo (`flask_size`, `cool_hours`, etc.).
    - `PlannerResource`: Capacidades diarias (tonelaje, cajas/día).
- **Solver**: Modela el problema como CSP (Constraint Satisfaction Problem) usando OR-Tools.
    - Minimiza cambios de patrón (setup costs).
    - Respeta fechas de entrega.
    - Respeta capacidad de líneas de moldeo.

---

## 4. Implementación y Estructura de Código

### Estructura de Proyecto
```
src/
    foundryplan/
        app.py          # Entry point, configuración de NiceGUI
        dispatcher/     # Dispatcher: colas ejecutables por proceso/línea
        data/           # Capa de acceso a datos (Repository pattern)
            db.py       # Definición de Schema SQLite
            repository.py # Todas las queries SQL
        planner/        # Módulo de planificación avanzada (OR-Tools)
        ui/             # Componentes visuales y páginas
```

### Principios de Desarrollo
1.  **Repository Pattern**: La UI nunca ejecuta SQL directo. Todo acceso a datos pasa por `Repository`.
2.  **Stateless Logic**: El `scheduler.py` debe ser funciones puras donde sea posible (Input List -> Output List).
3.  **Strict Types**: Uso intensivo de Type Hints (`mypy`).
4.  **Idempotencia**: Las operaciones de carga de datos (upsert) y migraciones de esquema (`ensure_schema`) deben ser seguras de re-ejecutar.

---

## 5. Especificaciones Detalladas (Planner Module)

### Definición del Problema
Planificar la producción de moldes semanalmente (Lunes a Domingo).
- **Unidad**: Moldes (no piezas individuales).
- **Restricción Crítica**: Cambiar de patrón (molde) en una línea es costoso. Se prefiere agrupar la producción de un mismo pedido.
- **Output**: Plan diario (`plan_daily_order`) indicando cantidad a moldear por `order_id` + `date`.

### Entidades Planner
- **Orders**: `(order_id, part_id, qty, due_date, priority)`
- **Parts**: `(part_id, flask_size, cool_hours, finish_hours, net_weight_ton, alloy)`
    - *Nota*: `finish_hours` se usa para estimar lag, `net_weight_ton` para restricción de tonelaje de vaciado.
- **Resources**: Capacidad por tamaño de caja (S/M/L) y total moldes/día.

### Flujo de Ejecución Planner
1. **Extract**: `repository.get_planner_inputs(scenario_id)` lee de tablas `sap_*` y `material_master`.
2. **Transform**: Convierte registros DB a dataclasses (`PlannerOrder`, etc.).
3. **Solve**: `planner.solve.run_solve(inputs)` ejecuta OR-Tools.
4. **Persist**: Guarda resultados en tablas `planner_outputs_*`.
