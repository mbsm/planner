# Foundry Plan — Manual de Desarrollo y Especificación Técnica

Este documento consolida la arquitectura, especificación, modelo de datos y detalles de implementación de Foundry Plan.

---

## 1. Visión Técnica

Foundry Plan es una aplicación web (NiceGUI) con backend Python y persistencia en SQLite para la planificación y despacho de producción en fundiciones "Make-to-Order".

### 1.1 Arquitectura
El sistema sigue una arquitectura modular en torno a un núcleo funcional:
- **UI (Frontend/Backend)**: `src/foundryplan/ui/` (NiceGUI). Renderizado servidor.
- **Dispatcher**: `src/foundryplan/dispatcher/` (Scheduler heurístico por proceso/recursos, genera colas ejecutables).
- **Planner Module**: `src/foundryplan/planner/` (Scheduler heurístico por capacidad, sin solver CP-SAT).
- **Data Access**: `src/foundryplan/data/` (Repositorio, DB Schema, Excel I/O).
- **DB Schema split**: `src/foundryplan/data/schema/` (`data_schema.py`, `dispatcher_schema.py`, `planner_schema.py`).
- **Persistencia**: SQLite local (`foundryplan.db`).

**Repositorios por módulo:**
- `Repository` es un *facade* que expone solo `repo.data`, `repo.dispatcher`, `repo.planner`.
- `repo.data`: snapshots SAP + maestro de materiales + config general.
- `repo.dispatcher`: colas/programas, locks “en proceso” y configuración de líneas.
- `repo.planner`: tablas y configuración del planner.

Dispatcher/Planner consultan órdenes/materiales vía `repo.data` y mantienen sus propias tablas internas.
En UI, cualquier lectura de órdenes, stock, desmoldeo o maestro debe ir por `repo.data`.

### 1.2 Tecnologías
- **Lenguaje**: Python 3.11+.
- **UI Framework**: NiceGUI (basado en FastAPI/Vue).
- **Base de Datos**: SQLite (con modo WAL estricto).
- **Planner**: Hoy usa heurística greedy; **CP-SAT (OR-Tools)** está planificado a futuro (no implementado aún).
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
    - `material_base` (Material base mapeado desde Vision via pedido/posición, usado para mapear moldes a piezas)
    - `centro`, `almacen` (Ubicación)
    - `lote` (Identificador único físico, usado para trazabilidad)
    - `documento_comercial`, `posicion_sd` (Enlace a pedido)
- **Filtros de Importación**:
    - **Centro**: Solo registros con `centro` = config `sap_centro` (default: "4000")
    - **Almacén**: Solo almacenes configurados en procesos activos (`process.sap_almacen` donde `is_active=1`)
      - Filtra materiales semi-elaborados que no son piezas finales
      - Si no hay procesos configurados, importa todos (compatibilidad)
    - **Mapeo material_base**: Durante importación, se mapea pedido/posición desde Vision para obtener material de pieza cuando el almacén tiene código de molde
- **Filtros de Disponibilidad por Proceso**:
    - El filtrado por estado (`libre_utilizacion`, `en_control_calidad`) se aplica **dinámicamente** según configuración de cada proceso (ver 2.2.C)
    - Cada proceso define su propio predicado de disponibilidad vía `process.availability_predicate_json`
    - Ejemplos:
        - Terminaciones: `{"libre_utilizacion": 1, "en_control_calidad": 0}` (stock disponible)
        - Toma de dureza: `{"libre_utilizacion": 0, "en_control_calidad": 1}` (stock bloqueado/QC)
    - Lotes alfanuméricos se marcan como `is_test=1`.

#### B. Visión Planta (Demand)
Representa la cartera de pedidos y fechas.
- **Tabla DB**: `sap_vision_snapshot`
- **Mapeo Clave**:
    - `pedido`, `posicion` (PK compuesta de la demanda)
    - `fecha_de_pedido` (Fecha comprometida con cliente, driver principal del plan)
    - `solicitado` (Cantidad original)
    - `peso_neto_ton` (Peso total del pedido) => Usado para calcular peso unitario
- **Filtros de Importación**:
    - **Prefijos Material**: Solo materiales que empiecen con prefijos configurados en `sap_vision_material_prefixes` (default: "401,402,403,404")
    - **Fecha**: `fecha_de_pedido > 2023-12-31`

#### C. Desmoldeo (WIP y Completadas)
Representa moldes en enfriamiento y piezas desmoldadas.
- **Tablas DB**: `core_moldes_por_fundir` (WIP), `core_piezas_fundidas` (completadas)
- **Mapeo Clave**:
    - `material` (Código de pieza extraído de "MOLDE PIEZA XXXXXXXXX")
    - `tipo_pieza` (Descripción original completa)
    - `flask_id` (ID físico de caja), `cancha` (Ubicación)
    - `demolding_date` (NULL en moldes_por_fundir, NOT NULL en piezas_fundidas)
    - `mold_quantity` (Fracción de caja por pieza: 0.25, 0.5, 1.0)
- **Filtros de Importación**:
    - **Campos obligatorios**: tipo_pieza, material, flask_id
    - **Canchas válidas**: Config `demolding_canchas_validas` (default: TCF-L1000..L3000, TDE-D0001..D0003)
    - **Extracción material**: Regex `(\d{11})(?:\D|$)` de campo Pieza
- **Separación**:
    - Sin `Fecha Desmoldeo` → `moldes_por_fundir`
    - Con `Fecha Desmoldeo` → `piezas_fundidas`
- **Auto-actualización core_material_master**:
    - `flask_size`: Desde ambas tablas (más reciente)
    - `tiempo_enfriamiento_molde_dias`, `piezas_por_molde`: **Solo desde core_piezas_fundidas**
    - **Status**: `status_comercial = 'activo'` (case-insensitive)
- **Actualización de Maestro**:
    - Durante importación, actualiza `material_master.peso_unitario_ton` = (peso_neto_kg/1000)/solicitado
    - Backfill de MB52: actualiza `material_base` en MB52 usando pedido/posición

**Condiciones iniciales (UI / planner):**
- `get_flask_usage_breakdown` agrega ocupación por tipo de caja usando prefijos de `planner_flask_types` (cae a primeros 3 chars o regex `L\d+`).
- **Moldes por Fundir (En Cancha)**: cada fila de `core_moldes_por_fundir` ocupa 1 caja; se agrupa por `flask_id` → tipo de caja y se muestra con `ceil`.
- **Piezas Fundidas (Enfriando/Desmoldeo pendiente)**: usa `mold_quantity` por `flask_id`; si `demolding_date` es futura, la caja se considera ocupada hasta `demolding_date + 1`; si es pasada o vacía, se asume liberación mañana (`today + 1`); se muestra con `ceil`.
- **Tons por Fundir**: suma de `peso_unitario_ton` desde `core_material_master` por molde en `core_moldes_por_fundir`, agrupado por tipo de caja.

#### C. Reporte Desmoldeo (WIP Enfriamiento)
Fuente SAP que informa qué moldes están actualmente en proceso de enfriamiento y cuándo se liberarán las cajas.
- **Tabla DB**: `sap_demolding_snapshot`
- **Mapeo Clave**:
    - `material` <= `Pieza`
    - `lote` <= `Lote`
    - `flask_id` <= **ID completo** de columna `Caja` (sin truncar)
    - `cancha` <= `Cancha` (ubicación física)
    - `demolding_date` <= `Fecha Desmoldeo` (**Dato real a usar, NO "Fecha a desmoldear"**)
    - `demolding_time` <= `Hora Desm.`
    - `mold_type` <= `Tipo molde`
    - `poured_date` <= `Fecha fundida`
    - `poured_time` <= `Hora Fundida`
    - `cooling_hours` <= `Hs. Enfria`
    - `mold_quantity` <= `Cant. Moldes` (entero)
- **Filtros de Importación**:
    - **Ninguno** - Se importa todo sin filtros
- **Filtros para Planner**:
    - `cancha` = config `planner_demolding_cancha` (default: "TCF-L1400")
    - Solo flasks con `demolding_date + 1 > hoy` (aún ocupadas)
- **Actualización Automática:**
    1. **Actualiza `material_master`:**
       - `flask_size` = Primeros 3 caracteres de `flask_id`
       - `tiempo_enfriamiento_molde_dias` = `cooling_hours` (horas)
       - `piezas_por_molde` = `ROUND(1.0 / mold_quantity)` (inverso redondeado)
         * Si `mold_quantity = 0.25` → `piezas_por_molde = 4`
         * Si `mold_quantity = 0.5` → `piezas_por_molde = 2`
         * Solo actualiza si `mold_quantity > 0`
    2. **Regenera `planner_daily_resources`:**
       - Reconstruye baseline desde config (turnos/feriados/capacidades)
       - Acumula fracciones de `mold_quantity` por día/flask_type
       - Descuenta cajas con `ceil()`: 0.75 cajas → 1 caja ocupada
       - Período: desde hoy hasta `demolding_date + 1`
       - Si fecha pasada → usa hoy como inicio
- **Campos a ignorar**: `Enfriamiento`, `Fecha a desmoldear`, `Colada`, `UA de Molde`, `Días para entregar`.

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

#### C. Configuración de Filtros de Disponibilidad por Proceso
Cada proceso puede tener filtros independientes para determinar qué stock del MB52 se considera "disponible" para ese proceso.

**Tabla**: `process`
**Campo**: `availability_predicate_json` (TEXT, JSON)

**Formato JSON**:
```json
{
  "libre_utilizacion": <0|1|null>,
  "en_control_calidad": <0|1|null>
}
```

**Comportamiento**:
- Si un campo está presente con valor 0 o 1, se filtra por ese valor exacto
- Si un campo es `null` o no está presente, NO se filtra por ese campo
- Los campos especificados se combinan con AND lógico

**Ejemplos de Configuración**:

| Proceso | `libre_utilizacion` | `en_control_calidad` | Significado |
|---------|---------------------|----------------------|-------------|
| Terminaciones | 1 | 0 | Solo stock libre y sin QC (disponible) |
| Toma de dureza | 0 | 1 | Solo stock bloqueado O en QC |
| Mecanizado | 1 | null | Solo libre, ignora QC |

#### D. Tabla de Recursos Diarios del Planner

**Tabla**: `planner_daily_resources`

Tabla clave que almacena disponibilidad real de recursos día a día, considerando configuración base y condiciones iniciales.

**Campos**:
- `scenario_id` (PK): Escenario de planner
- `day` (PK): Fecha ISO (YYYY-MM-DD)
- `flask_type` (PK): Tipo de caja (ej: "S", "M", "L")
- `available_qty`: Cajas disponibles (Total - Ocupadas)
- `molding_capacity_per_day`: Capacidad moldeo = molding_per_shift × turnos_día
- `same_mold_capacity_per_day`: Capacidad mismo molde = same_mold_per_shift × turnos_día
- `pouring_tons_available`: Toneladas fusión disponibles = pour_per_shift × turnos_día

**Generación Automática:**

1. **Baseline (Config + Turnos + Feriados):**
   - Ejecutado por: `rebuild_daily_resources_from_config()`
   - Horizonte: `min(planner_horizon_days, días_hasta_última_fecha_vision)`
   - Mínimo: 30 días, Máximo: según config (default 180)
   - Solo días laborables (días con turnos configurados - feriados)
   - Capacidades por día:
     ```
     molding = molding_per_shift × turnos_del_día
     same_mold = same_mold_per_shift × turnos_del_día
     pouring = pour_per_shift × turnos_del_día
     ```
   - Flasks: qty_total (completo)

2. **Actualización con Ocupación:**
   - Ejecutado por: `update_daily_resources_from_demolding()`
   - Lee: `sap_demolding_snapshot` filtrado por cancha
   - Lógica:
     ```python
     for cada línea desmoldeo:
         if demolding_date < hoy:
             demolding_date = hoy
         
         for día in range(hoy, demolding_date + 1):
             UPDATE planner_daily_resources
             SET available_qty = MAX(0, available_qty - mold_quantity)
             WHERE day = día AND flask_type = tipo_caja
     ```

**Triggers de Regeneración:**
- Al guardar Config > Planner → regenera baseline + aplica ocupación
- Al importar Desmoldeo → regenera baseline + aplica ocupación

**Uso:**
- Planner solver lee restricciones desde esta tabla
- UI muestra capacidades semanales agregando desde datos diarios
- Ocupación visible en "Condiciones Iniciales" = Total - Disponible
| Custom | null | 0 | Ignora libre, solo sin QC |

**Implementación**:
- `Repository._mb52_availability_predicate_sql(process)` lee la configuración y genera el SQL WHERE dinámicamente
- La UI en `/config` permite editar estos filtros por proceso mediante dropdowns (Cualquiera/Sí(1)/No(0))
- Default si no hay config: `{"libre_utilizacion": 1, "en_control_calidad": 0}` (solo stock disponible)

**Configuración en DB** (seeding automático):
```sql
INSERT INTO process(process_id, label, sap_almacen, availability_predicate_json) 
VALUES
  ('terminaciones', 'Terminaciones', '4035', '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
  ('toma_de_dureza', 'Toma de dureza', '4035', '{"libre_utilizacion": 0, "en_control_calidad": 1}'),
  ('mecanizado', 'Mecanizado', '4049', '{"libre_utilizacion": 1, "en_control_calidad": 0}'),
  ...
```

**Gestión desde UI**:
- Página: `/config` → Sección "Filtros de Disponibilidad por Proceso"
- Para cada proceso: editar Almacén, Libre utilización (dropdown), En control de calidad (dropdown)
- Botón "Guardar Filtros de Proceso" actualiza `process.sap_almacen` y `process.availability_predicate_json`
- Después de guardar, se ejecuta `kick_refresh_from_sap_all()` para regenerar jobs/programas

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
    - Se filtra `sap_mb52_snapshot` por `centro` (config `sap_centro`), `almacen = process.sap_almacen` y un predicado de disponibilidad configurable.
    - El predicado se lee desde `process.availability_predicate_json` (JSON con campos `libre_utilizacion` y/o `en_control_calidad`).
    - Esto permite que cada proceso tenga su propia regla (ej.: Terminaciones requiere stock disponible; Toma de Dureza requiere stock bloqueado).
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

### 3.2 Planner (Moldeo)
Responsable de la planificación de *Moldeo* (nivel orden, semanal).

- **Estado actual**: Implementado con heurística greedy (ver 3.2.2 Heurística). Usa capacidades diarias reales desde `planner_daily_resources` y condiciones iniciales.
- **Futuro (no implementado aún)**: CP-SAT con OR-Tools conforme al diseño descrito más abajo; se mantiene como diseño de referencia pero **no está activo**.
- **Ubicación**: `src/foundryplan/planner/`
- **Objetivo (común)**: Decidir cuántos moldes producir por día por pedido, buscando cumplir fechas y respetar capacidades y cajas.
- **Entradas**:
    - `PlannerOrder`: Pedidos pendientes (Visión) + `remaining_molds`.
    - `PlannerPart`: Atributos de moldeo (`flask_size`, `cool_hours`, `pieces_per_mold`, `finish_hours`, `min_finish_hours`).
    - `PlannerResource` / `planner_daily_resources`: Capacidades diarias (molding, same_mold, pouring, flasks) ya afectadas por desmoldeo.
    - `PlannerInitialConditions`: WIP actual (modelos cargados, flasks en uso, carga de colada).

#### 3.2.1 Diseño CP-SAT (futuro, no implementado)
Se mantiene como referencia para la evolución del planner, pero hoy no se ejecuta.

- **`remaining_molds`**: moldes pendientes por pedido.
- **Modelos (pattern) = `order_id`**: con penalidad por cambio de modelo y límite de modelos activos.
- **Cajas**: bloqueos por tipo/tamaño, usando desmoldeo para fechas de liberación.
- **Carga inicial de colada**: desde MB52 de moldes por fundir, forward-fill.
- **Restricciones previstas**: capacidades de moldeo, mismo molde, metal diario, cajas, y límites `finish_hours` / `min_finish_hours`.

Este diseño CP-SAT quedará para una fase futura; la implementación actual usa heurística greedy (ver 3.2.2).
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

#### 3.2.2 Heurística actual (implementada)
Ubicación: `src/foundryplan/planner/solve.py` (`solve_planner_heuristic`).

- **Capacidades por día**: lee `planner_daily_resources` ya descontado por desmoldeo (`update_daily_resources_from_demolding`). Para cada día usa:
  - `molding_capacity`
  - `same_mold_capacity`
  - `pouring_tons_available`
  - `flask_available` por tipo de caja
- **Orden de prioridad**: se calcula `start_by` estimando días de proceso (moldeo, fundición=1, enfriamiento=ceil(cool_hours/24), terminación=ceil(finish_hours/64), buffer de fin de semana). Se ordena:
  1) overdue (start_by <= 0)
  2) patrones cargados inicialmente (`initial_patterns_loaded`)
  3) `priority` ASC
  4) `start_by` ASC
- **Asignación diaria (greedy)**: recorre días y asigna moldes cumpliendo simultáneamente:
  - capacidad de moldeo del día
  - límite `same_mold_capacity` por parte en el día
  - límite de metal: `molds * (net_weight_ton * pieces_per_mold) <= pouring_tons_available`
  - cajas disponibles por tipo (ya descontadas por desmoldeo; se reduce al asignar)
- **Resultado**: `molds_schedule[order_id][day_idx] = qty`; marca `HEURISTIC_INCOMPLETE` si alguna orden queda con `qty_left > 0` (horizonte insuficiente).
- **No modela**: cambios de patrón, penalidades, ni finish_hours flexible; no usa CP-SAT.

#### 3.2.3 Supuestos de calendario (flujo de proceso)
- **Moldeo**: se moldean piezas el día $d$ (día hábil).
- **Fundición**: se funde el **siguiente día hábil**.
- **Enfriamiento**: desde el día de fundido, contar $\lceil \text{cool\_hours}/24 \rceil$ días **calendario**.
- **Desmoldeo**: ocurre el día siguiente al término del enfriamiento; las cajas retornan ese día.
- **Terminación**: desde desmoldeo, aplicar `finish_hours[o]` como **días hábiles**.
    - Valor **nominal** (desde `material_master`).
    - Reducible hasta `min_finish_hours[o]` (también desde `material_master`).
- **Bodega**: al día siguiente de terminar, las piezas llegan a bodega de producto terminado.
- **On-Time Delivery**: orden $o$ es **on-time** si todas sus piezas llegan a bodega en o antes de `due_date[o]`.

#### 3.2.2b Implementación del Calendario (Días Hábiles vs Calendario)

**Indexación de Tiempo:**
El planner usa un sistema de **índices de días hábiles** (workdays). La lista `workdays: list[date]` contiene solo fechas de lunes a viernes (excluyendo feriados configurados). Todos los cálculos y decisiones usan el índice en esta lista, no fechas calendario.

**Ejemplo:**
```
workdays[0] = 2026-02-02 (Lunes)
workdays[1] = 2026-02-03 (Martes)
workdays[2] = 2026-02-04 (Miércoles)
workdays[3] = 2026-02-05 (Jueves)
workdays[4] = 2026-02-06 (Viernes)
(Sábado y domingo omitidos)
workdays[5] = 2026-02-09 (Lunes siguiente)
```

**Ciclo de Vida del Molde (Workday-based):**

Para un molde moldado en `workdays[d]`:
- **Día d (Moldeo)**: Moldear en una línea
- **Día d+1 (Fundición)**: Verter metal, empezar enfriamiento
- **Días d+2 a d+1+cool_days (Enfriamiento)**: Flask bloqueada (ocupada)
  - Nota: `cool_days = ceil(cool_hours / 24)` tratado como **días hábiles** por simplificación
  - En la práctica, esto es conservador: el enfriamiento ocurre 24/7, pero asumimos como working days por simplicidad
- **Día d+2+cool_days (Desmoldeo)**: Sacar molde, liberar flask
- **Días d+3+cool_days a d+3+cool_days+finish_days (Terminación)**: Máquinas de acabado procesan piezas

**Duración total de lock de flask:**
$$\text{lock\_duration\_wd} = 2 + \text{cool\_days}$$

donde 2 = (moldeo + fundición) y `cool_days = ceil(cool_hours/24)`.

**Supuesto Simplificador (Decisión de Diseño):**
- **Moldeo, Fundición, Desmoldeo**: restricción de que ocurran en **días hábiles**
  - Se ModeloEstructura: no se schedula moldes para fin de semana
  - Fundición automáticamente salta al siguiente día hábil
- **Enfriamiento**: tratado como **días hábiles** (no como días calendario)
  - Ej: molde fundido viernes → enfriamiento viernes/lunes (salta fin de semana)
  - Esto es **conservador** (supone enfriamiento más lento de lo que realmente es)
  - Justificación: simplifica lógica CP-SAT y heurística; la precisión adicional de contabilizar fin de semana no compensa la complejidad

**Por Qué No Usar Calendario Completo para Enfriamiento:**

Usar calendario completo (24/7) requeriría:
1. Agregar lista de **todas las fechas calendario** (no solo hábiles) al solver
2. Crear función `get_next_workday_after_calendar_date()` para mapear cuándo termina el enfriamiento y cuándo desmoldear
3. Modificar constraint de flask: iterar sobre índices mixtos (hábil/calendario)
4. Complejidad O(n²) en lugar de O(n)

El trade-off: **Simplicidad vs Precisión**. Elegimos simplicidad porque:
- La planificación es semanal (horizonte ~8 semanas): el buffer es bajo
- La capacidad de flask raramente es bottleneck crítico
- El enfriamiento es 24/7 de todas formas (la máquina no se apaga), así que overestimar 1-2 días por fin de semana es tolerable

**Gestión de Feriados:**
- Config: `app_config.key='planner_holidays'` contiene lista JSON de fechas (ISO format: "2026-02-13", etc.)
- Función: `repository._planner_holidays() -> set[date]` carga la lista
- Aplicación: al construir `workdays` en `prepare_and_sync()`, se itera calendario y solo agrega días `d.weekday() < 5 and d not in holidays`

**Mapeo Demolding → Workday Index:**
Cuando se cargan moldes en proceso (Reporte Desmoldeo) con `demolding_date` (fecha real de desmoldeo):
```python
# En repository.get_planner_initial_flask_inuse_from_demolding()
release_date = demolding_date  # SAP ya da la fecha real de desmoldeo
workday_idx = 0
for d in date_range(asof_date, release_date):
    if d.weekday() < 5 and d not in holidays:
        workday_idx += 1
release_workday_index = workday_idx  # Índice hábil mapeado desde fecha calendario
```

**Archivos Relevantes:**
- `src/foundryplan/planner/solve.py`: Lógica de constraint (CP-SAT y heurística) usando índices workday
- `src/foundryplan/data/repository.py`: 
  - `prepare_and_sync()` línea ~1888: construye lista `workdays` filtrando weekdays + holidays
  - `get_planner_initial_flask_inuse_from_demolding()` línea ~1378: mapea demolding_date → workday_index
  - `_planner_holidays()`: carga feriados desde config

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

#### 3.2.7 Modelos/Patrones Cargados (Opcional)

La sección **"Modelos Cargados"** en la UI (`/plan` → card "Modelos cargados") permite marcar órdenes que tienen un modelo activo en la línea de moldeo hoy. Esta entrada es **completamente opcional** y **graceful degradation** está asegurada.

**Comportamiento:**

1. **Cuando se cargan patrones** (`initial_patterns_loaded = {order_id_1, order_id_2, ...}`):
   - **Heurístico**: Las órdenes en `initial_patterns_loaded` reciben `is_loaded = 0` en la función de ordenamiento (prioridad mayor).
     - Efecto: esas órdenes se procesan antes, minimizando cambios de modelo innecesarios.
   - **CP-SAT**: Las órdenes en `initial_patterns_loaded` no incurren en costo de "switch" el día 0 (si se activan ese día).
     - Efecto: el objetivo penaliza menos los cambios para órdenes nuevas vs órdenes que continúan.

2. **Cuando está vacío** (`initial_patterns_loaded = {}`):
   - **Heurístico**: Todas las órdenes reciben `is_loaded = 1` (iguales respecto a carga de patrón).
     - Efecto: la prioridad se define por `(overdue_status, priority, start_by)` solamente.
   - **CP-SAT**: Todas las órdenes incurren en costo de switch el día 0 si se activan.
     - Efecto: no hay reducción de costo para órdenes "anteriores"; todas compiten en igualdad de condiciones.
   - **Resultado**: El planner procede sin preferencia de patrones. No hay error ni excepción.

**UI:**
- Card marcada como "Opcional" (badge visible).
- Si el usuario no carga nada, mostrar lista vacía es válido.
- Al guardar, guardar un conjunto vacío es permitido.
- Próxima carga sin patrones sigue siendo graceful.

**Ubicación en código:**
- **Load/Save**: `src/foundryplan/ui/pages.py` línea ~906-1000
- **Repository fetch**: `src/foundryplan/data/repository.py` línea ~1630 (`get_planner_initial_patterns_loaded`)
- **Conversion to solver input**: `src/foundryplan/planner/api.py` línea ~157 (construye `initial_patterns_loaded` set)
- **Usage in solvers**:
  - Heurístico: `src/foundryplan/planner/solve.py` línea ~430 (función `_sort_key`)
  - CP-SAT: `src/foundryplan/planner/solve.py` línea ~255-256 (conteo de switches día 0)

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

## 5. Configuración del Sistema

### 5.1 Claves de Configuración Global (`app_config`)

Todas las configuraciones globales se almacenan en la tabla `app_config` con pares `(config_key, config_value)`.

#### Configuraciones SAP

| Clave | Descripción | Default | Notas |
|-------|-------------|---------|-------|
| `sap_centro` | Centro SAP para filtrar MB52 | `"4000"` | Solo se importa stock de este centro |
| `sap_vision_material_prefixes` | Prefijos de material para filtrar Vision Planta | `"401,402,403,404"` | Separados por comas. Solo Vision se filtra por prefijo |
| `sap_almacen_moldeo` | Almacén para proceso Moldeo | `"4032"` | Usado por el Planner |
| `sap_almacen_terminaciones` | Almacén para proceso Terminaciones | `"4035"` | Usado por el Dispatcher |
| `sap_almacen_toma_dureza` | Almacén para Toma de Dureza | `"4035"` | Mismo almacén que Terminaciones, diferente filtro de disponibilidad |
| `sap_almacen_mecanizado` | Almacén para Mecanizado | `"4049"` | |
| `sap_almacen_mecanizado_externo` | Almacén para Mecanizado Externo | `"4050"` | |
| `sap_almacen_inspeccion_externa` | Almacén para Inspección Externa | `"4046"` | |
| `sap_almacen_por_vulcanizar` | Almacén para Por Vulcanizar | `"4047"` | |
| `sap_almacen_en_vulcanizado` | Almacén para En Vulcanizado | `"4048"` | |

**Nota**: Los almacenes también se pueden configurar directamente en la tabla `process.sap_almacen`. Las claves `sap_almacen_*` en `app_config` sirven como fallback legacy.

#### Configuraciones de Prioridad

| Clave | Descripción | Default |
|-------|-------------|---------|
| `job_priority_map` | Mapeo de categorías a valores numéricos | `{"prueba": 1, "urgente": 2, "normal": 3}` |

#### Configuraciones del Planner

| Clave | Descripción | Default |
|-------|-------------|---------|
| `planner_weight_late_days` | Penalidad por día de retraso | `1000` |
| `planner_weight_finish_reduction` | Penalidad por reducir tiempo de finish | `50` |
| `planner_weight_pattern_changes` | Penalidad por cambio de modelo/patrón | `100` |
| `planner_solver_time_limit` | Tiempo máximo de solver (segundos) | `30` |
| `planner_solver_num_workers` | Número de workers para solver (0=auto) | `0` |
| `planner_solver_relative_gap` | Gap relativo de optimalidad | `0.01` |
| `planner_solver_log_progress` | Mostrar log de solver (0/1) | `0` |
| `planner_horizon_days` | Horizonte de planificación (días hábiles) | `30` |
| `planner_horizon_buffer_days` | Buffer adicional al horizonte | `10` |
| `planner_holidays` | Fechas de feriados (JSON array) | `[]` |

#### Configuraciones de UI

| Clave | Descripción | Default |
|-------|-------------|---------|
| `planta` | Nombre de la planta | `"Planta Rancagua"` |
| `ui_allow_move_in_progress_line` | Permitir mover items en proceso entre líneas | `"0"` |

### 5.2 Configuración de Procesos (`process`)

Cada proceso se configura en la tabla `process` con los siguientes campos:

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `process_id` | TEXT (PK) | Identificador único del proceso |
| `label` | TEXT | Nombre descriptivo para la UI |
| `sap_almacen` | TEXT | Código de almacén SAP asociado |
| `is_active` | INTEGER | 1=activo, 0=inactivo |
| `is_special_moldeo` | INTEGER | 1=proceso de moldeo (usa Planner), 0=proceso normal (usa Dispatcher) |
| `availability_predicate_json` | TEXT | JSON con filtros de disponibilidad (ver 5.2.1) |

#### 5.2.1 Filtros de Disponibilidad (`availability_predicate_json`)

Formato JSON para definir qué stock del MB52 se considera disponible para cada proceso:

```json
{
  "libre_utilizacion": <0|1|null>,
  "en_control_calidad": <0|1|null>
}
```

**Reglas**:
- Si un campo tiene valor 0 o 1: se filtra por ese valor exacto (`WHERE campo = valor`)
- Si un campo es `null` o no está presente: NO se filtra por ese campo
- Los campos presentes se combinan con AND lógico

**Ejemplos**:

| Configuración | SQL Generado | Uso Típico |
|---------------|--------------|------------|
| `{"libre_utilizacion": 1, "en_control_calidad": 0}` | `WHERE libre_utilizacion=1 AND en_control_calidad=0` | Terminaciones (stock disponible) |
| `{"libre_utilizacion": 0, "en_control_calidad": 1}` | `WHERE libre_utilizacion=0 AND en_control_calidad=1` | Toma de dureza (stock bloqueado) |
| `{"libre_utilizacion": 1}` | `WHERE libre_utilizacion=1` | Solo verificar libre, ignorar QC |
| `{}` o `null` | `WHERE 1=1` | No filtrar (tomar todo) |

**Gestión desde UI**:
- Página: `/config` → Sección "Filtros de Disponibilidad por Proceso"
- Dropdowns por proceso: "Libre utilización" (Cualquiera/Sí(1)/No(0)), "En control de calidad" (Cualquiera/Sí(1)/No(0))
- Botón "Guardar Filtros de Proceso" actualiza la configuración y regenera jobs

### 5.3 Edición de Configuración desde la UI

#### Configuración Global (`/config`)

**Sección: Parámetros Generales**
- Nombre Planta
- Centro SAP
- Prefijos Material (Visión Planta)
- UI: Mover filas 'en proceso'

**Sección: Mapeo de Almacenes SAP**
- Grid con inputs para cada proceso (Terminaciones, Mecanizado, Moldeo, etc.)
- Botón "Guardar Cambios Globales"

**Sección: Filtros de Disponibilidad por Proceso**
- Grid con 4 columnas: Proceso, Almacén, Libre utilización, En control de calidad
- Dropdowns para seleccionar filtros (Cualquiera/Sí/No)
- Botón "Guardar Filtros de Proceso"

#### Configuración del Planner (`/config/planner`)

- Pesos de optimización (Late days, Finish reduction, Pattern changes)
- Parámetros del solver (Time limit, Workers, Gap, Log)
- Horizonte de planificación
- Feriados (lista editable de fechas)

---

## 6. Especificaciones Detalladas (Planner Module)

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
