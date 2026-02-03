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
- **Objetivo**: Decidir cuántos moldes producir por día por pedido, optimizando entrega a tiempo, minimizando cambios de modelo y uso de capacidad reducida.
- **Entradas**:
    - `PlannerOrder`: Pedidos pendientes (Visión) + `remaining_molds`.
    - `PlannerPart`: Atributos de moldeo (`flask_size`, `cool_hours`, `pieces_per_mold`, `finish_hours`, `min_finish_hours`).
    - `PlannerResource`: Capacidades diarias (molding, pouring, flasks).
    - `PlannerInitialConditions`: WIP actual (modelos cargados, flasks en uso, carga de colada).
- **Solver**: Modela el problema como CSP usando OR-Tools CP-SAT.
    - Maximiza entrega a tiempo, con penalidades por cambios de modelo y reducción de tiempos.

#### 3.2.1 Decisiones de modelado (Moldeo)
- **`remaining_molds`**: representa *moldes pendientes de fabricar* para el pedido (no hechos aún).
- **Modelos (pattern) = `order_id`**: un modelo puede servir a varias órdenes, pero la política de cambio es por orden.
    - **Regla blanda (soft)**: preferir terminar la orden antes de cambiar modelo; se modela como penalidad en el objetivo.
    - **Límite duro**: máximo 6 modelos (órdenes) activos en paralelo.
    - **Finish before switch**: una orden debe tener `remaining_molds = 0` antes de desactivar su modelo.
- **Uso de cajas (flasks)**:
    - **Fuente**: Reporte Desmoldeo (no MB52). La fecha de liberación de la caja se deriva del desmoldeo/enfriamiento reportado.
    - **Persistencia**: se carga en `planner_initial_flask_inuse` con `release_workday_index`.
- **Carga inicial de colada (pour load)**:
    - Se calcula desde MB52 (todos los moldes fabricados **no fundidos**).
    - **Metal por molde** = `net_weight_ton × pieces_per_mold`.
    - Se distribuye **ASAP** llenando la capacidad diaria hacia adelante (forward fill) y se guarda en `planner_initial_pour_load`.
- **Restricción de colada por día (hard)**:
    - $$\sum_o \text{molds}_{o,d} \times (\text{net\_weight\_ton}_o \times \text{pieces\_per\_mold}_o) \le \text{pour\_max\_ton\_per\_day} - \text{initial\_pour\_load}_d$$
- **Tiempos de terminación (flexible, dentro de límites)**:
    - Cada orden tiene `finish_hours` nominal (fijo en `material_master`).
    - Puede reducirse hasta `min_finish_hours` para respetar fecha comprometida.
    - Si incluso con reducción máxima no se alcanza la fecha, la orden se marca **late (atrasada)**.

#### 3.2.2 Supuestos de calendario (flujo de proceso)
- **Moldeo**: se moldean piezas el día $d$ (día hábil).
- **Fundición**: se funde el **siguiente día hábil**.
- **Enfriamiento**: desde el día de fundido, contar $\lceil \text{cool\_hours}/24 \rceil$ días **calendario**.
- **Desmoldeo**: ocurre el día siguiente al término del enfriamiento; las cajas retornan ese día.
- **Terminación**: desde desmoldeo, aplicar `finish_hours[o]` como **días hábiles**.
    - Valor **nominal** (desde `material_master`).
    - Reducible hasta `min_finish_hours[o]` (también desde `material_master`).
- **Bodega**: al día siguiente de terminar, las piezas llegan a bodega de producto terminado.
- **On-Time Delivery**: orden $o$ es **on-time** si todas sus piezas llegan a bodega en o antes de `due_date[o]`.

#### 3.2.3 Formulación matemática del Solver

**Variables de decisión:**
- `molds[o, d]` ∈ ℤ⁺ := moldes de orden $o$ a moldear el día hábil $d$
- `finish_hours_real[o]` ∈ ℝ := horas de terminación **reales** asignadas a orden $o$
  - Restricción: `min_finish_hours[o] ≤ finish_hours_real[o] ≤ nominal_finish_hours[o]`
- `pattern_active[o, d]` ∈ {0,1} := modelo de orden $o$ activo en día $d$
- `completion_day[o]` ∈ ℤ := día en que la última pieza de orden $o$ llega a bodega
- `on_time[o]` ∈ {0,1} := 1 si `completion_day[o] ≤ due_date[o]`, 0 en caso contrario

**Restricciones Hard:**

1. **Cobertura de moldes**: 
   $$\sum_d \text{molds}[o,d] = \text{remaining\_molds}[o] \quad \forall o$$

2. **Capacidad moldeo por día**: 
   $$\sum_o \text{molds}[o,d] \le \text{molding\_max\_per\_day} \quad \forall d$$

3. **Capacidad moldeo por part/día**: 
   $$\text{molds}[o,d] \le \text{molding\_max\_same\_part\_per\_day} \quad \forall o, d$$

4. **Capacidad metal por día (considerando WIP inicial)**:
   $$\sum_o \text{molds}[o,d] \times (\text{net\_weight}[o] \times \text{pieces\_per\_mold}[o])$$
   $$\le \text{pour\_max\_ton\_per\_day} - \text{initial\_pour\_load}[d] \quad \forall d$$

5. **Disponibilidad de cajas por tamaño** (RESTRICCIÓN CRÍTICA - cuello de botella de planta):
   - Existen $n$ tamaños de cajas independientes: `flask_size` ∈ {"800", "1200", "1600", ...}
   - Cada tamaño tiene su inventario total: `flask_inventory[flask_size]` (ej: 50 cajas de "800", 30 de "1200")
   - Cada parte usa **siempre** la misma caja: `part.flask_size` es fijo
   - Las restricciones son **independientes** entre tamaños (las cajas no se comparten entre tamaños diferentes)
   - Para cada tamaño $s$ y día $d$:
     $$\text{initial\_flask\_inuse}[s,d] + \sum_{o \in \text{orders\_by\_flask}[s]} \sum_{p=0}^{d} \mathbb{1}[\text{is\_cooling}(o,p,d)] \times \text{molds}[o,p] \le \text{flask\_inventory}[s]$$
6. **Modelo activo solo si hay moldes**:
    - `pattern_active[o,d] = 1` ⟺ `molds[o,d] > 0`
    - Esta variable se usa para contar cambios de modelo en la función objetivo
7. **Finish hours bounds**:
   $$\text{min\_finish\_hours}[o] \le \text{finish\_hours\_real}[o] \le \text{nominal\_finish\_hours}[o] \quad \forall o$$

8
8. **Finish hours bounds**:
   $$\text{min\_finish\_hours}[o] \le \text{finish\_hours\_real}[o] \le \text{nominal\_finish\_hours}[o] \quad \forall o$$

9. **Completion day computation**:
   - Sea `last_mold_day[o]` = último día en que se moldea molde de orden $o$
   - Sea `pour_day[o]` = `last_mold_day[o] + 1` (día hábil siguiente)
   - Sea `cool_calendar_days[o]` = $\lceil \text{cool\_hours}[o]/24 \rceil$
   - Sea `demolding_day[o]` = `pour_day[o] + cool_calendar_days[o] + 1` (día calendario siguiente al enfriamiento)
   - Sea `finish_workdays[o]` = $\lceil \text{finish\_hours\_real}[o]/24 / 8 \rceil$ (días hábiles, asumiendo 8h/día)
   - Sea `finish_day[o]` = `demolding_day[o]` + `finish_workdays[o]` (convertir a días hábiles)
   - `completion_day[o]` = `finish_day[o] + 1` (día siguiente a terminar, piezas en bodega)
9. **Late days computation**:
   $$\text{late\_days}[o] = \max(0, \text{completion\_day}[o] - \text{due\_day}[o]) \quad \forall o$$
10. **On-Time definition**:
    $$\text{on\_time}[o] = 1 \text{ si } \text{completion\_day}[o] \le \text{due\_date}[o] \text{, else } 0$$

**Función Objetivo (MINIMIZAR, lineal):**

$$\text{minimize} = w_{\text{late\_days}} \cdot \sum_o \text{late\_days}[o]$$
$$+ w_{\text{finish\_reduction}} \cdot \sum_o (\text{nominal\_finish\_hours}[o] - \text{finish\_hours\_real}[o])$$
$$+ w_{\text{pattern\_changes}} \cdot \text{num\_pattern\_switches}$$

> Nota: se reemplaza **on-time delivery** por **late days** para mantener el problema **lineal y manejable** con el horizonte largo.

Donde:
- `late_days[o] = max(0, completion_day[o] - due_date[o])` (linealizable con variables auxiliares).
- `num_pattern_switches` = número de veces que `pattern_active[o, d] = 1` y `pattern_active[o, d-1] = 0` (cambios de 0→1).
- `w_late_days`, `w_finish_reduction`, `w_pattern_changes` son **parámetros configurables desde la GUI** (pesos/penalties).

#### 3.2.4 Parámetros configurables (UI)
Almacenados en `app_config` o tabla dedicada `planner_config`:
- `planner_weight_late_days`: penalidad por días de atraso (default: 1000)
- `planner_weight_finish_reduction`: penalidad por reducción de tiempos (default: 50)
- `planner_weight_pattern_changes`: costo fijo por cambio de modelo (default: 100)
- `planner_solver_time_limit`: tiempo máximo del solver (segundos, default: 30)
- `planner_solver_num_workers`: número de workers CP-SAT (0 = auto, default: 0)
- `planner_solver_relative_gap`: límite de gap relativo para convergencia (default: 0.01)
- `planner_solver_log_progress`: log de búsqueda (0/1, default: 0)
- `planner_horizon_days`: horizonte de planificación (días hábiles, default: 30)
- `planner_horizon_buffer_days`: buffer calendario extra para cálculos (días, default: 10)
- `planner_holidays`: conjunto de fechas no laborales (texto con fechas, separadas por coma o línea)

**Auto-Horizonte (v2)**:
- UI calcula automáticamente `horizonte_sugerido = index(última_due_date) + 10% buffer`
- Usuario ve propuesta en label "📅 Horizonte sugerido: N días"
- Puede aceptar o modificar manualmente
- Retorno de `run_planner()` incluye:
  - `suggested_horizon_days`: horizonte calculado desde órdenes
  - `actual_horizon_days`: horizonte usado en ejecución

#### 3.2.5 Implicancias en inputs
- `planner_parts` debe incluir:
    - `pieces_per_mold` (moldes x piezas)
    - `finish_hours` (nominal, desde `material_master`)
    - `min_finish_hours` (mínimo reducible, desde `material_master`)
    - `cool_hours` (horas de enfriamiento en molde, desde `material_master`)
    - `net_weight_ton` (peso unitario en toneladas)
- `planner_orders` incluye `due_date` para cálculo de `start_by` y entregas.
- `planner_resources` incluye `molding_max_per_day`, `molding_max_same_part_per_day`, `pour_max_ton_per_day`, `flasks_S/M/L`.
- `planner_initial_order_progress` → `remaining_molds` (derivado de Vision)
- `planner_initial_patterns_loaded` → entrada del usuario (qué órdenes tienen modelo activo hoy)
- `planner_initial_flask_inuse` → desde Reporte Desmoldeo
- `planner_initial_pour_load` → desde MB52 (WIP no fundido)

#### 3.2.6 Enfoques de planificación (Optimización vs Heurístico)

**A) Optimizador (OR-Tools)**
- El backlog puede ser 14–18 semanas, pero el tiempo real de fabricación por orden es 3–6 semanas.
- Se resuelve el plan en un horizonte configurable (30 días hábiles por defecto). *Arquitectura preparada para bloques secuenciales futuros.*
- Cada bloque puede propagar su salida como condición inicial del siguiente:
    - flasks en uso, carga de colada pendiente y órdenes parcialmente moldeadas.
- Supuesto de complejidad: resolver **n problemas de tamaño t/n** suele ser más rápido que 1 problema de tamaño t.
- Esto permite responder preguntas de negocio:
    - “¿Cuándo puedo entregar este pedido?”
    - “¿Qué pedidos se afectan si fuerzo uno nuevo a una fecha?”

**B) Heurístico (Greedy capacity-first con start_by mejorado)**

*Algoritmo mejorado (v2)*:
- **Cálculo de `start_by` por orden** (fecha de inicio recomendada):
  $$\text{start\_by} = \text{due\_date} - \left(\begin{array}{l}
    \lceil\frac{\text{remaining\_molds}}{\text{molding\_max\_same\_part\_per\_day}}\rceil + \\
    1 + \\
    \lceil\frac{\text{cool\_hours}}{24}\rceil + \\
    \lceil\frac{\text{finish\_hours}}{8 \times 24}\rceil + \\
    \lceil\frac{\text{total\_process\_days}}{7} \times 2\rceil
  \end{array}\right)$$
  
  Donde:
  - Semanas de moldeo = $\lceil\frac{\text{remaining\_molds}}{\text{molding\_max\_same\_part\_per\_day}}\rceil$
  - Pouring = 1 día hábil
  - Cooling = $\lceil\frac{\text{cool\_hours}}{24}\rceil$ días calendario
  - Finish = $\lceil\frac{\text{finish\_hours}}{8 \times 24}\rceil$ días hábiles (asumiendo 8h/día)
  - Weekend buffer = $\lceil\frac{\text{process\_days}}{7} \times 2\rceil$ (2 días por cada 7 de proceso)

- **Orden de procesamiento** (prioridad de scheduling):
  1. Órdenes con `start_by <= hoy` (atrasadas) — máxima urgencia
  2. Órdenes con modelo/patrón activo (minimiza cambios)
  3. Por prioridad ASC (1=urgente, 3=normal)
  4. Por `start_by` ASC (fechas más próximas)

- **Capacidad diaria**: 
  - Moldeo: `molding_max_per_day` global + `molding_max_same_part_per_day` por parte
  - Cajas: Inventario S/M/L respetando días de enfriamiento
  - Metal: `pour_max_ton_per_day` (menos WIP inicial)

- **Garantía de cobertura**: 
  - El heurístico intenta schedular TODOS los moldes faltantes en el horizonte.
  - Si no cabe: retorna `status=HEURISTIC_INCOMPLETE` con lista de órdenes sin schedular.
  - Lanza error si horizonte > 365 días (evita problemas de memoria/complejidad).

- **Auto-horizonte**:
  - UI calcula horizonte sugerido = index(última due_date) + 10% buffer
  - Usuario puede aceptar o modificar manualmente.

Este enfoque es rápido (greedy O(n log n)) y explicable, aunque no garantiza optimalidad global.

**C) Combinado (Heurístico + Solver)**
- Ejecuta heurístico primero → extrae solución como warm-start hints para CP-SAT
- Pasa hints a CP-SAT para refinamiento/optimización
- Permite convergencia más rápida del solver con mejor punto inicial factible

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
- **Restricción Crítica**: Cambiar de modelo (molde) en una línea es costoso. Se prefiere agrupar la producción de un mismo pedido.
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
