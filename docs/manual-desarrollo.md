# Foundry Plan — Manual de Desarrollo y Especificación Técnica

Este documento consolida la arquitectura, especificación, modelo de datos y detalles de implementación de Foundry Plan.

---

## 1. Visión Técnica

Foundry Plan es una aplicación web (NiceGUI) con backend Python y persistencia en SQLite para la planificación y despacho de producción en fundiciones "Make-to-Order".

### 1.1 Arquitectura
El sistema sigue una arquitectura modular en torno a un núcleo funcional:
- **UI (Frontend/Backend)**: `src/foundryplan/ui/` (NiceGUI). Renderizado servidor.
- **Dispatcher**: `src/foundryplan/dispatcher/` (Scheduler heurístico por proceso/recursos, genera colas ejecutables).
- **Planner Module**: `src/foundryplan/planner/` (Scheduler heurístico por capacidad).
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
- **Planner**: Heurística greedy basada en capacidad.
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
    - `material` (Código de semi-elaborado)
    - `material_base` (Número de pieza - mapeado desde Vision via pedido/posición, usado para mapear moldes a piezas)
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
    - **Atributos Moldeo**: `flask_size` (código numérico: 105, 120, 143, etc), `piezas_por_molde`, `tiempo_enfriamiento_molde_horas`, `aleacion`.
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
- El planner lee restricciones desde esta tabla
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

**Prioridad (dispatcher_orderpos_priority)**: Tabla compartida entre Dispatcher y Planner
- **Tabla DB:** `dispatcher_orderpos_priority` (accessed via view `orderpos_priority`)
- **Primary Key:** `(pedido, posicion)`
- **Campos:**
  - `is_priority`: Booleano (0/1) indicando si la orden está marcada como urgente
  - `kind`: Tipo de prioridad ("test", "manual", "" para normal)
- **Mapeo a prioridad numérica** (compartido entre Dispatcher y Planner):
  - `kind = "test"` → `priority = 1` (máxima urgencia - lotes de prueba)
  - `is_priority = 1` (o `kind != ""` y no test) → `priority = 2` (urgente - marcadas manualmente)
  - Resto → `priority = 3` (normal)
- **Comportamiento:**
  - Usuario marca órdenes como "urgentes" desde UI (cualquier vista)
  - Marking se persiste en `dispatcher_orderpos_priority`
  - Ambos Dispatcher y Planner consultan esta tabla para asignar prioridad
  - Garantiza que órdenes urgentes se procesan primero en ambos módulos

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

- **Entradas**:
    - `PlannerOrder`: Pedidos pendientes (Visión) + `remaining_molds`.
    - `PlannerPart`: Atributos de moldeo (`flask_size`, `cool_hours`, `pieces_per_mold`, `finish_hours`, `min_finish_hours`).
    - `PlannerResource` / `planner_daily_resources`: Capacidades diarias (molding, same_mold, pouring, flasks) ya afectadas por desmoldeo.
    - `PlannerInitialConditions`: WIP actual (modelos cargados, flasks en uso, carga de colada).


#### 3.2.1 Heurística de Planificación con Sliding Window

**Ubicación:** `src/foundryplan/planner/solve.py` → `solve_planner_heuristic()`

**Algoritmo:**

La heurística usa un enfoque **greedy con búsqueda de ventanas** (sliding window search) que intenta colocar cada orden lo más pronto posible respetando todas las restricciones.

**1. Ordenamiento de Órdenes**

Función: `sort_orders_for_planning()`

Criterios de prioridad (orden lexicográfico):
1. `priority` ASC (1=Urgente, 2=Normal)
2. `order_id` ASC (desempate estable)


**2. Capacidades Diarias**

Lee `planner_daily_resources` (ya descontado por desmoldeo/enfriamiento):
- `molding_capacity`: Capacidad total de moldeo por día
- `same_mold_capacity`: Máximo del mismo material por día
- `pouring_tons_available`: Toneladas de fusión disponibles
- `flask_available[flask_type]`: Cajas disponibles por tipo

**3. Búsqueda de Placement (Sliding Window)**

Para cada orden en orden de prioridad:

```python
def find_placement_for_order(..., max_search_days=365):
    """
    Busca la primera ventana viable para moldear completo.
    Intenta días: 0, 1, 2, ..., hasta max_search_days.
    """
    for attempt_day in range(0, min(horizon, max_search_days)):
        result = try_place_order(start_day_idx=attempt_day, ...)
        if result.success:
            return result
    return FAILURE
```

**4. Constraints de Placement**

Función: `try_place_order(start_day_idx, ...)` 

Valida simultáneamente:

a) **Capacidad de moldeo general**: `qty_day <= molding_capacity[day]`

b) **Capacidad mismo molde**: `qty_day <= same_mold_capacity[day]`

c) **Capacidad de vaciado**: `qty_day × metal_per_mold <= pouring_tons[pour_day]`

d) **Disponibilidad de flasks en TODA la ventana** (crítico):
   ```python
   pour_day = mold_day + pour_lag_days
   release_day = pour_day + cooling_days + shakeout_lag_days
   
   # Valida disponibilidad desde mold_day hasta release_day (inclusive)
   flask_window_min = min(
       flask_available[flask_type][d] 
       for d in range(mold_day, release_day + 1)
   )
   
   qty_feasible = min(..., flask_window_min)
   ```

e) **Contiguidad** (si `allow_molding_gaps=False`):
   - Una vez iniciado moldeo, debe continuar días consecutivos hasta completar
   - Si un día no tiene capacidad → placement falla
   - Si `allow_molding_gaps=True` → puede saltar días sin capacidad

**Lags configurables:**
- `pour_lag_days`: Moldeo → Fundición (default: 1)
- `shakeout_lag_days`: Enfriamiento → Desmoldeo (default: 1)

**5. Optimización de Finishing Hours**

Función parte de `try_place_order()`:

```python
# Calcular completion con finish_hours nominal
finish_days_nominal = ceil(finish_hours / 24)
completion_nominal = last_release_day + finish_days_nominal

# Si nos pasamos del due_date, comprimir hasta min_finish_hours
if completion_nominal > due_day_idx:
    available_finish_days = max(0, due_day_idx - last_release_day)
    available_finish_hours = available_finish_days × 24
    
    # Comprimir pero no menos de min_finish_hours
    finish_hours_effective = max(min_finish_hours, available_finish_hours)

# Calcular completion_day con finish_hours_effective
finish_days = ceil(finish_hours_effective / 24)
completion_day = last_release_day + finish_days
```

**Casos de uso:**
- **Tiempo suficiente**: `finish_hours_effective = finish_hours` (nominal)
- **Compresión necesaria**: `finish_hours_effective` entre `min_finish_hours` y `finish_hours`
- **Atraso inevitable**: Usa `min_finish_hours` pero `completion_day > due_date`

**6. Parámetros Configurables**

**Tabla:** `planner_resources`

| Parámetro | Tipo | Descripción | Default |
|-----------|------|-------------|---------|
| `max_placement_search_days` | INTEGER | Máximo días de búsqueda de ventana | 365 |
| `allow_molding_gaps` | INTEGER (0/1) | Permitir huecos en moldeo | 0 |

Estos parámetros se configuran desde UI en Config > Planner > "Algoritmo de Placement".

**7. Salida del Planner**

```python
{
    "status": "HEURISTIC" | "HEURISTIC_INCOMPLETE",
    "molds_schedule": {order_id: {day_idx: qty_molds}},
    "pour_days": {order_id: [day_idx, ...]},
    "shakeout_days": {order_id: day_idx},
    "completion_days": {order_id: day_idx},
    "finish_hours": {order_id: finish_hours_effective},  # ⭐ Puede ser < nominal
    "late_days": {order_id: days_late},
    "errors": ["Order X: reason", ...],
}
```

**8. Ventajas de la Heurística**

✅ **Simplicidad**: Greedy O(n log n), rápido incluso con cientos de órdenes  
✅ **Explicabilidad**: Fácil entender por qué una orden se coloca en cierto día  
✅ **Respeto de constraints**: Valida todas las restricciones en cada paso  
✅ **Flexibilidad**: Parámetros configurables desde GUI  
✅ **Optimización de tiempos**: Reduce finishing automáticamente para cumplir fechas  

**9. Limitaciones**

❌ **No óptimo globalmente**: Decisiones greedy pueden bloquear soluciones mejores  
❌ **Sensible a orden**: El orden de priorización afecta resultado final  
❌ **No backtracking**: Una vez asignada, no remueve decisiones previas  

Para optimización global futura, ver Anexo A.

#### 3.2.2 Supuestos de Calendario (Flujo de Proceso)
- **Moldeo**: se moldean piezas el día $d$ (día hábil).
- **Fundición**: ocurre en $d + \text{pour\_lag\_days}$ (default 1). El consumo de metal se descuenta solo ese día.
- **Enfriamiento + Desmoldeo**: las cajas permanecen bloqueadas desde moldeo hasta $d + \text{pour\_lag\_days} + \lceil \text{cool\_hours}/24 \rceil + \text{shakeout\_lag\_days}$ (inclusive).
- **Terminación**: desde el día de desmoldeo, se aplican `finish_hours[o]` como días hábiles; puede reducirse hasta `min_finish_hours[o]` para cumplir `due_date`.
- **On-Time Delivery**: orden $o$ es **on-time** si su `completion_day` (terminación) ocurre en o antes de `due_date[o]`.

#### 3.2.2b Implementación del Calendario (Días Hábiles vs Calendario)

**Indexación de Tiempo:**
El planner usa un sistema de **índices de días hábiles** (workdays). La lista `workdays: list[date]` contiene solo fechas en que hay turnos configurados (excluyendo feriados configurados). Todos los cálculos y decisiones usan el índice en esta lista, no fechas calendario.

**Ejemplo (calendario con turnos lunes a viernes):**
```
workdays[0] = 2026-02-02 (Lunes)
workdays[1] = 2026-02-03 (Martes)
workdays[2] = 2026-02-04 (Miércoles)
workdays[3] = 2026-02-05 (Jueves)
workdays[4] = 2026-02-06 (Viernes)
workdays[5] = 2026-02-09 (Lunes siguiente)
```

**Nota:** Si se configuran turnos para sábados en `planner_daily_resources`, esos días también aparecerán en `workdays`.

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
- **Moldeo, Fundición, Desmoldeo**: restricción de que ocurran en **días hábiles** (días con turnos configurados)
  - Fundición automáticamente salta al siguiente día hábil
- **Enfriamiento**: tratado como **días hábiles** (no como días calendario)
  - Ej: si turnos lunes-viernes, molde fundido viernes → enfriamiento viernes/lunes (salta fin de semana)
  - Esto es **conservador** (supone enfriamiento más lento de lo que realmente es)
  - Justificación: simplifica lógica heurística; la precisión adicional de contabilizar calendario completo no compensa la complejidad

**Por Qué No Usar Calendario Completo para Enfriamiento:**

Usar calendario completo (24/7) requeriría:
1. Agregar lista de **todas las fechas calendario** (no solo hábiles) a la heurística
2. Crear función `get_next_workday_after_calendar_date()` para mapear cuándo termina el enfriamiento y cuándo desmoldear
3. Modificar constraint de flask: iterar sobre índices mixtos (hábil/calendario)
4. Complejidad O(n²) en lugar de O(n)

El trade-off: **Simplicidad vs Precisión**. Elegimos simplicidad porque:
- La planificación es semanal
- El enfriamiento es 24/7 de todas formas, así que overestimar 1-2 días por semana genera un poco mas de holgura en la operacion sin aumentar la complejidad de la implementación actual.

**Gestión de Calendario y Feriados:**
- **Días laborables**: Determinados por `planner_daily_resources.workday=1` (días con turnos configurados)
- **Feriados**: Config `app_config.key='planner_holidays'` contiene lista JSON de fechas (ISO format: "2026-02-13", etc.)
- Función: `repository._planner_holidays() -> set[date]` carga la lista
- Aplicación: al construir `workdays` en `prepare_and_sync()`, se itera calendario y solo agrega días con turnos configurados (excluyendo feriados)

**Mapeo Desmoldeo Calendario → Workday Index:**
Cuando se cargan moldes en proceso (Reporte Desmoldeo) con `demolding_date` (fecha real de desmoldeo):
```python
# En repository.get_planner_initial_flask_inuse_from_demolding()
release_date = demolding_date  # SAP ya da la fecha real de desmoldeo
workday_idx = 0
for d in date_range(asof_date, release_date):
    if is_workday(d, daily_resources) and d not in holidays:
        workday_idx += 1
release_workday_index = workday_idx  # Índice hábil mapeado desde fecha calendario
```

**Archivos Relevantes:**
- `src/foundryplan/planner/solve.py`: Lógica heurística usando índices workday
- `src/foundryplan/data/repository.py`: 
  - `prepare_and_sync()` línea ~1888: construye lista `workdays` desde `planner_daily_resources` (días con workday=1, excluyendo feriados)
  - `get_planner_initial_flask_inuse_from_demolding()` línea ~1378: mapea demolding_date → workday_index
  - `_planner_holidays()`: carga feriados desde config

#### 3.2.3 Parámetros configurables (UI)
Almacenados en `planner_resources` (tabla única de configuración):

**Capacidades Diarias:**
- `molding_per_shift`: Moldeos por turno (default: 8)
- `same_mold_per_shift`: Moldeos mismo molde por turno (default: 4)
- `pour_per_shift`: Toneladas fusión por turno (default: 10)
- `shifts_per_day`: Turnos por día (default: 3)
- `flask_total_{size}`: Cajas totales por tamaño (105, 120, 143, 161, 185, 210)

**Algoritmo de Placement:**
- `max_placement_search_days`: Máximo días de búsqueda de ventana (default: 365)
- `allow_molding_gaps`: Permitir huecos en moldeo (0/1, default: 0)
- `pour_lag_days`: Días entre moldeo y fundición (default: 1)
- `shakeout_lag_days`: Días entre fundición y desmoldeo (default: 1)

**Horizonte y Calendario:**
- `planner_horizon_days`: Horizonte de planificación (días hábiles, default: 30)
- `planner_holidays`: Conjunto de fechas no laborales (JSON array)

**Auto-Horizonte:**
- UI calcula automáticamente `horizonte_sugerido = días_hasta_última_orden + 10% buffer`
- Usuario ve propuesta en label "📅 Horizonte sugerido: N días"
- Puede aceptar o modificar manualmente
- La consulta limita órdenes hasta `min(planner_horizon_days, días_hasta_última_orden)`

#### 3.2.4 Implicancias en inputs
- `planner_parts` debe incluir:
    - `pieces_per_mold` (moldes x piezas)
    - `finish_hours` (nominal, desde `material_master`)
    - `min_finish_hours` (mínimo reducible, desde `material_master`)
    - `cool_hours` (horas de enfriamiento en molde, desde `material_master`)
    - `net_weight_ton` (peso unitario en toneladas)
- `planner_orders` incluye `due_date` para cálculo de `start_by` y entregas.
- `planner_resources` incluye `molding_max_per_day`, `molding_max_same_part_per_day`, `pour_max_ton_per_day`, cantidades por tipo de caja (105, 120, 143, etc).
- `planner_initial_order_progress` → `remaining_molds` (derivado de Vision)
- `planner_initial_patterns_loaded` → entrada del usuario (qué órdenes tienen modelo activo hoy)
- `planner_initial_flask_inuse` → desde Reporte Desmoldeo
- `planner_initial_pour_load` → desde MB52 (WIP no fundido)

#### 3.2.5 Enfoques de planificación (Heurístico)

La implementación actual usa un algoritmo heurístico greedy basado en capacidad.

**Algoritmo heurístico (Greedy capacity-first con start_by mejorado)**:
- **Cálculo de `start_by` por orden** (fecha de inicio recomendada):
  $$\text{start\_by} = \text{due\_date} - \left(\begin{array}{l}
    \lceil\frac{\text{remaining\_molds}}{\text{molding\_max\_same\_part\_per\_day}}\rceil + \\
    1 + \\
    \lceil\frac{\text{cool\_hours}}{24}\rceil + \\
    \lceil\frac{\text{finish\_hours}}{8 \times 24}\rceil + \\
    \lceil\frac{\text{total\_process\_days}}{7} \times 2\rceil
  \end{array}\right)$$
  
  Donde:
  - Dias de moldeo = $\lceil\frac{\text{remaining\_molds}}{\text{molding\_max\_same\_part\_per\_day}}\rceil$
  - Pouring = 1 día hábil
  - Cooling = $\lceil\frac{\text{cool\_hours}}{24}\rceil$ días calendario
  - Finish = $\lceil\frac{\text{finish\_hours}}{8 \times 24}\rceil$ días hábiles (asumiendo 8h/día)
  - Weekend buffer = $\lceil\frac{\text{process\_days}}{7} \times 2\rceil$ (2 días por cada 7 de proceso)

- **Orden de procesamiento** (prioridad de scheduling):
  1. Por prioridad ASC (1=urgente/test, 2=normal)
     - Prioridad compartida con Dispatcher (misma tabla `dispatcher_orderpos_priority`)
     - Usuario marca órdenes urgentes desde UI → aplica en ambos módulos
  2. Por `order_id` ASC (tiebreaker estable)

- **Capacidad diaria**: 
  - Moldeo: `molding_max_per_day` global + `molding_max_same_part_per_day` por parte
  - Cajas: Inventario por código de caja (105, 120, 143, etc) respetando días de enfriamiento
  - Metal: `pour_max_ton_per_day` (menos WIP inicial)

- **Garantía de cobertura**: 
  - El heurístico intenta schedular TODOS los moldes faltantes en el horizonte.
  - Si no cabe: retorna `status=HEURISTIC_INCOMPLETE` con lista de órdenes sin schedular.
  - Lanza error si horizonte > 365 días (evita problemas de memoria/complejidad).

- **Auto-horizonte**:
  - UI calcula horizonte sugerido = index(última due_date) + 10% buffer
  - Usuario puede aceptar o modificar manualmente.

Este enfoque es rápido (greedy O(n log n)) y explicable, aunque no garantiza optimalidad global.

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
        planner/        # Módulo de planificación heurística
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

**Tabla:** `planner_resources` (registro único con todas las configuraciones)

**Capacidades:**

| Campo | Descripción | Default |
|-------|-------------|---------|
| `molding_per_shift` | Moldeos por turno | `8` |
| `same_mold_per_shift` | Moldeos mismo molde por turno | `4` |
| `pour_per_shift` | Toneladas fusión por turno | `10` |
| `shifts_per_day` | Turnos por día | `3` |
| `flask_total_{size}` | Cajas totales (por tamaño: 105, 120, 143, 161, 185, 210) | Varía |

**Algoritmo:**

| Campo | Descripción | Default |
|-------|-------------|---------|
| `max_placement_search_days` | Máximo días búsqueda de ventana | `365` |
| `allow_molding_gaps` | Permitir huecos en moldeo (0/1) | `0` |
| `pour_lag_days` | Días moldeo → fundición | `1` |
| `shakeout_lag_days` | Días fundición → desmoldeo | `1` |

**Horizonte:**

| Clave (app_config) | Descripción | Default |
|-------|-------------|---------|
| `planner_horizon_days` | Horizonte de planificación (días hábiles) | `30` |
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

**Sección: Capacidades Diarias**
- Moldeos por turno (general)
- Moldeos mismo molde por turno
- Toneladas fusión por turno
- Turnos por día
- Cajas totales por tamaño (105, 120, 143, 161, 185, 210)

**Sección: Algoritmo de Placement**
- Máximo días de búsqueda de ventana
- Permitir huecos en moldeo (checkbox)
- Días lag: Moldeo → Fundición, Fundición → Desmoldeo

**Sección: Horizonte y Calendario**
- Horizonte de planificación (días hábiles)
- Feriados (lista editable de fechas)

Botón "Guardar Configuración" actualiza `planner_resources` y regenera `planner_daily_resources`.

---

## 6. Especificaciones Detalladas (Planner Module)

La implementación vigente usa la heurística descrita en 3.2.2 (capacidades diarias + tramo contiguo).

Flujo actual (heurística):
1. Extract: inputs y recursos diarios (`planner_daily_resources`).
2. Solve: `solve_planner_heuristic` asigna moldes con las restricciones diarias ya descontadas.
3. Persist/Output: `molds_schedule` por `order_id` y `day_idx`; estado HEURISTIC/INCOMPLETE.
4. Persist: resultado completo se guarda en `planner_schedule_results` para visualización posterior.



### 6.1 Estructura de Salida del Planner (Schedule Result)

El resultado de `solve_planner_heuristic()` y `run_planner()` es un diccionario que se **persiste automáticamente** en la tabla `planner_schedule_results`. Este diseño permite que la UI muestre siempre el último plan sin necesidad de re-ejecutar la heurística.

#### Estructura del Dict Resultado

```python
result = {
    # Meta información
    "run_timestamp": str,  # ISO timestamp (solo en resultados cargados de DB)
    "status": str,  # "HEURISTIC" | "HEURISTIC_INCOMPLETE"
    "suggested_horizon_days": int | None,  # Horizonte calculado desde última due_date
    "actual_horizon_days": int,  # Horizonte usado en ejecución
    "skipped_orders": int,  # Cantidad de órdenes excluidas (sin flask capacity)
    "horizon_exceeded": bool,  # True si hay errores (órdenes sin schedular)
    
    # Schedule principal (órdenes programadas)
    "molds_schedule": {
        "<order_id>": {
            <day_idx>: <qty_molds>,  # int -> int (día de trabajo -> cantidad de moldes)
            ...
        },
        ...
    },
    
    # Días críticos por orden
    "pour_days": {
        "<order_id>": [<day_idx>, ...],  # Días de fundición (puede haber múltiples)
    },
    "shakeout_days": {
        "<order_id>": <day_idx>,  # Día de desmoldeo (liberación de cajas)
    },
    "completion_days": {
        "<order_id>": <day_idx>,  # Día de finalización (desmoldeo + finishing)
    },
    
    # Métricas de calidad
    "finish_days": {
        "<order_id>": <days>,  # Días de finishing usados (int, puede ser < nominal si se comprimió)
    },
    "late_days": {
        "<order_id>": <days>,  # Días de atraso vs due_date (0 si on-time)
    },
    
    # Errores y diagnóstico
    "errors": [
        "Orden X: Dato faltante: flask_type",
        "Orden Y: No se encontró ventana viable buscando 365 días desde HOY",
        "Orden Z: Flask type 143 sin capacidad disponible (revisar maestro de materiales)",
        ...
    ],
    
    # Objetivo (siempre None en heurística)
    "objective": None,  # Reservado para solver matemático futuro
}
```

#### Persistencia en Base de Datos

**Tabla: `planner_schedule_results`**

```sql
CREATE TABLE planner_schedule_results (
    scenario_id INTEGER NOT NULL,
    run_timestamp TEXT NOT NULL,              -- ISO timestamp de ejecución
    asof_date TEXT NOT NULL,                  -- Fecha base del plan
    status TEXT NOT NULL,                     -- "HEURISTIC" | "HEURISTIC_INCOMPLETE"
    suggested_horizon_days INTEGER,
    actual_horizon_days INTEGER NOT NULL,
    skipped_orders INTEGER NOT NULL,
    horizon_exceeded INTEGER NOT NULL,        -- 1 si hay errores, 0 si ok
    molds_schedule_json TEXT,                 -- JSON: {order_id: {day_idx: qty}}
    pour_days_json TEXT,
    shakeout_days_json TEXT,
    completion_days_json TEXT,
    finish_days_json TEXT,
    late_days_json TEXT,
    errors_json TEXT,
    objective REAL,
    PRIMARY KEY (scenario_id, run_timestamp)
);
```

**Funciones (planner/persist.py):**
- `save_schedule_result()`: Guarda resultado completo tras `run_planner()`
- `get_latest_schedule_result()`: Carga último schedule guardado
- `delete_old_schedule_results()`: Auto-limpieza (mantiene últimos 10)

**Flujo:**
1. Usuario ejecuta "Regenerar y planificar" en UI (`/plan`)
2. `run_planner()` ejecuta la heurística
3. El resultado completo se guarda automáticamente en `planner_schedule_results`
4. Se eliminan runs antiguos (mantiene últimos 10)
5. Al abrir `/plan`, la UI carga y muestra el último schedule guardado

#### Validación Fail-Fast (Sin Defaults)

**CRÍTICO**: El planner NO asume defaults. Si falta un dato requerido, la orden va a `errors[]`:

| Campo              | Validación                          | Error si Falta/Inválido |
|--------------------|-------------------------------------|-------------------------|
| `flask_type`       | `!= None and != ""`                 | "Dato faltante: flask_type" |
| `cool_hours`       | `> 0`                               | "Dato faltante o inválido: cool_hours=X" |
| `finish_days`      | `> 0`                               | "Dato faltante o inválido: finish_days=X" |
| `min_finish_days`  | `> 0`                               | "Dato faltante o inválido: min_finish_days=X" |
| `pieces_per_mold`  | `> 0`                               | "Dato faltante o inválido: pieces_per_mold=X" |
| `net_weight_ton`   | `> 0`                               | "Dato faltante o inválido: net_weight_ton=X" |

**Origen de datos:** `core_material_master` (por `part_code` consolidado).

**Recomendación:** Antes de ejecutar planner, verificar que todas las piezas tengan datos completos en maestro.

---

## 7. Interfaz de Usuario (GUI)

La aplicación usa **NiceGUI** (framework basado en FastAPI + Vue) para renderizar todas las páginas. La UI es servidor-side rendering con componentes reactivos.

### Arquitectura UI

**Entry Point:** `src/foundryplan/ui/pages.py` - función `register_pages(repo: Repository)`
- Cada página es una función decorada con `@ui.page("/ruta")`
- Recibe `repo` via closure desde `app.py`
- Renderiza usando componentes NiceGUI (`ui.label`, `ui.table`, `ui.button`, etc.)

**Widgets Reutilizables:** `src/foundryplan/ui/widgets.py`
- `render_nav()`: Barra de navegación superior
- `page_container()`: Contenedor principal con padding/max-width
- Tablas con double-click handlers, filtros, etc.

### Páginas Principales

#### `/` - Dashboard (Home)

**Propósito:** Vista general del estado de producción semanal.

**Funcionalidad:**
- Muestra calendario semanal (semana actual + 5 semanas siguientes)
- Filtra por proceso (terminaciones, mecanizado, mecanizado_externo, etc.)
- Resalta pedidos atrasados (due_date < hoy)
- Tabla para cada semana con columnas:
  - Lote, Cantidad, Quincena a despachar, Urgencia, Días atrasados
  - Iconos: 🔴 atrasado, ⚠️ test, 📦 en proceso
- **Double-click en fila** → abre modal con breakdown SAP (MB52 + Vision)
- Paginación: usa tabs de NiceGUI para navegar entre semanas

**Elementos interactivos:**
- Select process: dropdown con lista de procesos
- Tabs semana_0 a semana_5
- Tablas con sort/filter automático
- Modal popup con detalle SAP al hacer double-click

**Código:** `pages.py` línea ~117-817

#### `/plan` - Planificador de Producción (Moldeo)

**Propósito:** Ejecutar y visualizar el plan heurístico de moldeo.

**Funcionalidad:**
- **Condiciones Iniciales** (primera card):
  - Muestra cajas ocupadas hoy por flask_type
  - Basado en reporte de desmoldeo (moldes por fundir + piezas fundidas)
  - Calcula release_date considerando cool_hours
- **Recursos y Capacidades** (segunda card):
  - Tabla semanal con capacidades disponibles
  - Filas: Moldeo, Mismo molde, Colada (tons), Cajas por tipo
  - Capacidades ya descontadas por ocupación inicial
- **Plan Guardado** (tercera card):
  - Muestra último schedule guardado en DB (`planner_schedule_results`)
  - Tabla semanal: Total Moldes, Toneladas, Cajas por tipo
  - Timestamp de última ejecución
  - Lista de errores y órdenes omitidas
- **Botón "Regenerar y planificar"**:
  - Regenera `planner_daily_resources` desde config + desmoldeo
  - Ejecuta `run_planner()` → heurística greedy
  - Guarda resultado en DB
  - Actualiza UI con nuevo plan

**Elementos interactivos:**
- Input scenario (default: "default")
- Botón refresh (icon=refresh)
- Botón "Regenerar y planificar" (color=primary)
- 3 contenedores reactivos (initial_conditions, resources, plan)

**Código:** `pages.py` línea ~818-1362

#### `/actualizar` - Carga de Datos SAP

**Propósito:** Importar snapshots de Excel (MB52, Vision, Desmoldeo).

**Funcionalidad:**
- **MB52 Upload**:
  - Lee Excel (sheet "Hoja1")
  - Normaliza columnas (`excel_io.normalize_excel_mb52`)
  - Reemplaza `core_sap_mb52_snapshot`
  - Genera automáticamente `core_orders` reconciliando con Vision
- **Vision Upload**:
  - Lee Excel (sheet "Hoja1")
  - Normaliza columnas
  - Reemplaza `core_sap_vision_snapshot`
  - Filtra por alloy catalog (solo aleaciones configuradas)
  - Regenera `core_orders`
- **Desmoldeo Upload**:
  - Lee Excel (sheets múltiples: "Moldes por Fundir", "Piezas Fundidas")
  - Extrae `part_code` de material (5 dígitos)
  - Auto-completa `core_material_master` con datos faltantes
  - Reemplaza `core_moldes_por_fundir` y `core_piezas_fundidas`
  - Regenera `planner_daily_resources`
- **Botón "Actualizar Todo"**:
  - Regenera orders desde MB52+Vision para todos los procesos
  - Regenera programas Dispatcher para todos los procesos
  - Muestra resumen de jobs generados

**Elementos interactivos:**
- 3 upload controls (MB52, Vision, Desmoldeo)
- Botón "Actualizar Todo" (regenera orders + programs)
- Logs de auditoría tras cada operación
- Notificaciones de éxito/error

**Código:** `pages.py` línea ~1467-1798

#### `/familias` - Maestro de Familias

**Propósito:** Gestionar agrupaciones de piezas por familia.

**Funcionalidad:**
- Tabla editable con familias existentes
  - Columnas: family_id, nombre, descripción
  - Edición inline con doble-click
- CRUD completo:
  - Agregar nueva familia (dialog modal)
  - Editar nombre/descripción
  - Eliminar familia (confirma si tiene parts asociados)
- Auto-inferencia de familia desde descripción:
  - Botón "Inferir Familias desde Descripción"
  - Usa regex patterns para detectar familias en `descripcion_pieza`
  - Propone asignaciones automáticas
  - Usuario confirma antes de aplicar

**Elementos interactivos:**
- Tabla con columnas editables
- Botón "Nueva Familia" → dialog
- Botón "Inferir Familias" → proceso automático
- Botón eliminar por fila

**Código:** `pages.py` línea ~1799-1990

#### `/config` - Configuración General

**Propósito:** Administrar parámetros globales del sistema.

**Funcionalidad:**
- **Sección: Parámetros Generales** (`app_config`)
  - Nombre de planta
  - Centro SAP (filtro MB52)
  - Prefijos material (Visión Planta)
  - Aleaciones activas (multi-select desde catálogo)
- **Sección: Mapeo de Almacenes SAP**
  - Grid con inputs para cada proceso
  - Define qué almacén SAP corresponde a cada proceso
  - Ejemplo: terminaciones → "4040,4050"
- **Sección: Filtros de Disponibilidad por Proceso**
  - Define condición SQL para filtrar MB52
  - Dropdowns: Libre utilización (Cualquiera/Sí/No), Control calidad (Cualquiera/Sí/No)
  - Genera JSON: `{"libre_utilizacion": 1, "en_control_calidad": 0}`
- **Botón "Guardar Cambios Globales"**:
  - Actualiza todas las config en `core_config`
  - Regenera filtros availability_predicate_json

**Elementos interactivos:**
- Inputs text para cada parámetro
- Select para aleaciones
- Grid de almacenes (proceso × almacén)
- Dropdowns para filtros MB52
- Botón guardar

**Código:** `pages.py` línea ~1392-1466

#### `/config/aleaciones` - Catálogo de Aleaciones

**Propósito:** Gestionar aleaciones disponibles en planta.

**Funcionalidad:**
- Tabla con aleaciones del catálogo
  - Columnas: alloy_code, nombre, descripción, activo
  - Solo aleaciones activas se usan para filtrar Vision
- CRUD completo:
  - Agregar nueva aleación
  - Editar nombre/descripción
  - Activar/desactivar (checkbox)
  - Eliminar aleación

**Código:** `pages.py` línea ~1991-2191

#### `/config/tiempos` - Tiempos de Proceso por Familia

**Propósito:** Configurar tiempos estándar (vulcanizado, mecanizado, inspección) por familia.

**Funcionalidad:**
- Tabla con familias y sus tiempos en días
  - Columnas editable inline
  - Valores en días (INT)
- Impacto: usado por Dispatcher para calcular `start_by` de jobs
- Validación: días >= 0

**Código:** `pages.py` línea ~2192-2204

#### `/config/materiales` - Maestro de Materiales (part_code)

**Propósito:** Gestionar datos maestros consolidados por código de parte (5 dígitos).

**Funcionalidad:**
- Búsqueda por part_code o descripción
- Vista/edición de datos maestros:
  - Pieza: descripción, familia, aleación
  - Moldeo: flask_size, piezas_por_molde, cool_hours
  - Terminación: finish_days, min_finish_days
  - Mecanizado: mecanizado_dias, inspeccion_externa_dias
  - Vulcanizado: vulcanizado_dias
  - Peso: peso neto (tons)
- **Auto-completado**:
  - Al importar Desmoldeo → extrae part_code y crea registros faltantes
  - Al guardar → valida consistencia
- **Edición inline**:
  - Doble-click en fila → modal de edición
  - Inputs para cada campo
  - Validación antes de guardar

**Elementos interactivos:**
- Input búsqueda (part_code / descripción)
- Tabla con paginación
- Modal edición con tabs (Pieza, Moldeo, Terminación, Mecanizado)
- Botón guardar

**Código:** `pages.py` línea ~2205-2658

#### `/config/planner` - Configuración del Planner (Moldeo)

**Propósito:** Configurar parámetros del scheduler heurístico de moldeo.

**Funcionalidad:**
- **Sección: Capacidades y Turnos**
  - Moldeo por turno, Colada por turno
  - Mismo molde por turno
  - Turnos por día de semana (lun-dom)
  - Capacidades diarias calculadas automáticamente (capacidad × turnos)
- **Sección: Inventario de Cajas por Tipo**
  - Tabla editable: flask_type, qty_total, codes_csv
  - Códigos SAP (ej: "105,106,107")
- **Sección: Algoritmo de Placement**
  - Máximo días de búsqueda de ventana (`max_placement_search_days`)
  - Permitir huecos en moldeo (checkbox)
  - Días lag: Moldeo → Fundición, Fundición → Desmoldeo
- **Sección: Horizonte y Calendario**
  - Horizonte de planificación (días hábiles)
  - Feriados (lista editable de fechas ISO: "2026-02-13")
- **Sección: Ocupación de Recursos (Desmoldeo)**
  - Configurar cancha para filtrar reporte desmoldeo
  - Ejemplo: "TCF-L1000,TCF-L1100,TCF-L1200"
- **Botón "Guardar Configuración"**:
  - Actualiza `planner_resources`
  - Regenera `planner_daily_resources` desde config

**Elementos interactivos:**
- Inputs numéricos para capacidades
- Grid de turnos (día × shifts)
- Tabla de cajas (editable)
- Input horizonte (días)
- Textarea feriados (comma-separated)
- Checkboxes para algoritmo

**Código:** `pages.py` línea ~2659-3015

#### `/config/dispatcher` - Configuración de Líneas Dispatcher

**Propósito:** Configurar líneas de trabajo y restricciones para Dispatcher.

**Funcionalidad:**
- **Por proceso** (terminaciones, mecanizado, etc.):
  - Tabla de líneas (line_id, label, familias permitidas, orden)
  - CRUD completo: agregar, editar, eliminar, reordenar
  - Familias permitidas: multi-select (restringe qué jobs puede tomar cada línea)
- **Validación**:
  - line_id único por proceso
  - Orden de líneas afecta prioridad de asignación en scheduler
- **Impacto**:
  - Dispatcher usa esta config para generar colas ejecutables
  - Jobs van solo a líneas con familia compatible

**Código:** `pages.py` línea ~3016-3300

#### `/programa/<proceso>` - Programas de Producción (Dispatcher)

**Propósito:** Visualizar colas de trabajo generadas por Dispatcher.

**Rutas:**
- `/programa` (redirige a terminaciones)
- `/programa/toma-de-dureza`
- `/programa/mecanizado`
- `/programa/mecanizado-externo`
- `/programa/inspeccion-externa`
- `/programa/por-vulcanizar`
- `/programa/en-vulcanizado`

**Funcionalidad:**
- **Vista principal:**
  - Una card por línea (ej: "T1 - Terminaciones Línea 1")
  - Tabla de jobs en orden de ejecución
  - Columnas: Lote, Cantidad, Quincena, Urgencia, Start By, Días p/ entregar
- **Pestañas:**
  - Programa: jobs asignados por línea
  - No programadas: jobs sin línea compatible (errores)
  - Detalles: errors del scheduler
- **Jobs "En Proceso"**:
  - Fijados al inicio de su línea (pin icon 📌)
  - No se reordenan en re-generación
  - Usuario puede marcar/desmarcar "en proceso" desde tabla
- **Timestamp:**
  - "Última regeneración: 2026-02-07 14:30:15"
- **Botón "Forzar Regeneración"**:
  - Reconstruye orders desde SAP
  - Re-ejecuta scheduler
  - Actualiza UI

**Elementos interactivos:**
- Tabs por línea + "No programadas"
- Tablas con sort/filter
- Checkbox "en proceso" por job (toggle)
- Botón regenerar

**Código:** `pages.py` línea ~3768-3900+

#### `/audit` - Auditoría

**Propósito:** Bitácora de operaciones del sistema.

**Funcionalidad:**
- Tabla con últimas 500 operaciones
  - Columnas: timestamp, categoría, mensaje, detalles
  - Categorías: import, config, planner, dispatcher, error
- No editable (solo lectura)
- Útil para troubleshooting

**Código:** `pages.py` línea ~1363-1391

#### `/db` - Administración de Base de Datos

**Propósito:** Operaciones de bajo nivel sobre SQLite (administrador).

**Funcionalidad:**
- **Vacuum**: compactar DB
- **Backup**: generar copia de seguridad
- **Ver esquema**: lista de tablas y columnas
- **Query directo**: ejecutar SQL arbitrario (solo lectura)
- **Peligroso**: solo para debugging

**Código:** `pages.py` línea ~3301-3376

### Componentes Reutilizables (widgets.py)

**`render_nav(active: str, repo: Repository)`**
- Barra de navegación superior
- Links a todas las páginas principales
- Resalta página activa
- Sticky top

**`page_container()`**
- Context manager para contenido principal
- Padding y max-width consistentes
- Centra contenido

**Otros Widgets:**
- `excel_upload()`: Component para subir Excel
- `confirm_dialog()`: Modal de confirmación
- `edit_table_cell()`: Edición inline de celdas
- `date_picker()`: Selector de fecha (NiceGUI nativo)

---

## 8. Changelog y Evolución del Sistema

### 8.1 Migración finish_hours → finish_days (2026-02-07)

**Resumen:** Refactorización completa para cambiar almacenamiento de tiempos de terminación de **horas** a **días**. Se eliminaron defaults automáticos (fail-fast validation).

#### Cambios en Código

**1. Modelo de Datos (`planner/model.py`)**
- `PlannerPart.finish_hours: float` → `finish_days: int`
- `PlannerPart.min_finish_hours: float` → `min_finish_days: int`

**2. Solver (`planner/solve.py`)**
- `PlacementResult.finish_hours_effective: float` → `finish_days_effective: int`
- **Validación fail-fast** (líneas 66-90): si falta dato → error "Dato faltante o inválido: finish_days=X"
- **Optimización de finishing** (líneas 237-255): comprime `finish_days` hasta `min_finish_days` para cumplir `due_date`
- **Retorno** (línea 554): `"finish_hours"` → `"finish_days"`

**3. API (`planner/api.py`)**
- Construcción de parts (líneas 101-111): elimina conversión días→horas
- `build_orders_plan_summary()` (líneas 318-394): `finish_hours_nominal` → `finish_days_nominal`

**4. Repository (`planner/planner_repository.py`)**
- `sync_planner_inputs_from_sap()` (líneas 1190-1220): NO aplica defaults
- `replace_planner_parts()`, `get_planner_parts_rows()`: columnas `finish_days`, `min_finish_days`

**5. Schema (`data/schema/planner_schema.py`)**
- Nuevas columnas: `finish_days INTEGER`, `min_finish_days INTEGER`
- Migración automática desde `finish_hours` (división por 24)
- Nuevas columnas de lag: `pour_lag_days`, `shakeout_lag_days`

#### Validación de Datos (Fail-Fast)

| Campo              | Validación | Error si Inválido |
|--------------------|------------|-------------------|
| `flask_type`       | `!= None and != ""` | "Dato faltante: flask_type" |
| `cool_hours`       | `> 0` | "Dato faltante o inválido: cool_hours=X" |
| `finish_days`      | `> 0` | "Dato faltante o inválido: finish_days=X" |
| `min_finish_days`  | `> 0` | "Dato faltante o inválido: min_finish_days=X" |
| `pieces_per_mold`  | `> 0` | "Dato faltante o inválido: pieces_per_mold=X" |
| `net_weight_ton`   | `> 0` | "Dato faltante o inválido: net_weight_ton=X" |

**Comportamiento:** Orden con dato faltante → NO se planifica, se agrega a `errors[]`, UI muestra en "Órdenes No Planificadas".

#### Impacto en Documentación

- **manual-desarrollo.md**: Actualizado con algoritmo heurístico, validación fail-fast
- **schedule-output.md**: Creado (estructura dict resultado, persistencia)
- **CAMBIOS-finish-days.md**: Este documento (consolidado aquí)

### 8.2 Persistencia de Schedule en DB (2026-02-07)

**Resumen:** Implementación de persistencia automática del schedule del planner en tabla `planner_schedule_results`.

#### Cambios

**1. Nueva Tabla (`planner_schema.py`)**
```sql
CREATE TABLE planner_schedule_results (
    scenario_id INTEGER NOT NULL,
    run_timestamp TEXT NOT NULL,
    asof_date TEXT NOT NULL,
    status TEXT NOT NULL,
    molds_schedule_json TEXT,
    pour_days_json TEXT,
    shakeout_days_json TEXT,
    completion_days_json TEXT,
    finish_days_json TEXT,
    late_days_json TEXT,
    errors_json TEXT,
    PRIMARY KEY (scenario_id, run_timestamp)
);
```

**2. Módulo de Persistencia (`planner/persist.py`)**
- `save_schedule_result()`: guarda resultado completo
- `get_latest_schedule_result()`: carga último schedule
- `delete_old_schedule_results()`: auto-cleanup (mantiene últimos 10)

**3. API (`planner/api.py`)**
- `run_planner()` ahora guarda automáticamente el resultado
- Importa funciones de `persist.py`

**4. Repository (`planner/planner_repository.py`)**
- Nuevo método `get_latest_schedule_result()`

**5. UI (`ui/pages.py`)**
- Nueva función `_render_last_saved_plan()`: carga y muestra último schedule guardado
- Al abrir `/plan` → muestra automáticamente último plan (sin re-ejecutar heurística)
- Timestamp visible: "Última ejecución: YYYY-MM-DDTHH:MM:SS"

#### Ventajas

✅ Plan persiste entre sesiones
✅ UI lista al abrir (no necesita recalcular)
✅ Historial de últimas 10 ejecuciones
✅ Trazabilidad completa



---

## Anexo A: Diseño CP-SAT (implementación futura planificada)

Este anexo documenta una posible evolución del sistema hacia optimización matemática mediante CP-SAT (Constraint Programming - Satisfiability) de Google OR-Tools. Esta implementación **no está activa en el código actual** y se conserva como blueprint para una fase posterior del proyecto.

**Motivación**: La heurística greedy actual es rápida y explicable, pero no garantiza optimalidad global. Para escenarios complejos con múltiples restricciones conflictivas, un solver matemático podría encontrar mejores soluciones.

**Diseño propuesto**:
- **Definición del problema**: Plan semanal de moldes; unidad = moldes; preferir continuidad de modelo; output diario `plan_daily_order`.
- **Entidades**: Orders `(order_id, part_id, qty, due_date, priority)`; Parts `(flask_size, cool_hours, finish_hours, min_finish_hours, net_weight_ton, pieces_per_mold, alloy)`; Resources (capacidad por caja y tonelaje diario).
- **Condiciones iniciales**: flasks ocupadas desde desmoldeo, carga de colada inicial, patrones cargados.
- **Restricciones previstas**: capacidad de moldeo, mismo molde, metal diario, flasks por tamaño, límites `finish_hours/min_finish_hours`, penalidad/costo por cambio de patrón, horizonte y feriados.
- **Flujo CP-SAT**: Extract → Transform → Solve (OR-Tools) → Persist (`planner_outputs_*`).

**Estado**: Documentación de diseño únicamente. Implementación pendiente para futuras iteraciones del sistema.
