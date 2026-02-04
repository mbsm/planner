# Foundry Plan — Modelo de Datos

Este documento detalla el esquema de base de datos, mapeo desde fuentes SAP y campos de tablas maestras.

## 1. Fuentes de Datos Externas (Excel/SAP)

### 1.1 Reporte Stock MB52

Representa el inventario físico por lote en almacenes seleccionados.
Cada carga reemplaza completamente los datos anteriores ("snapshot").

**Tabla DB:** `sap_mb52_snapshot`

| Campo DB | Columna Excel (Normalizada) | Descripción | Mapeo / Regla |
|---|---|---|---|
| `material` | `material` | Número de parte | Copia directa |
| `texto_breve` | `texto_breve_de_material` | Descripción | Copia directa |
| `centro` | `centro` | Centro SAP | Copia directa |
| `almacen` | `almacen` | Almacén SAP | Copia directa |
| `lote` | `lote` | Identificador de lote | Copia directa |
| `pb_almacen` | `pb_a_nivel_de_almacen` | Peso bruto (informativo) | Copia directa |
| `libre_utilizacion` | `libre_utilizacion` | Indicador de disponibilidad | Mapeo directo (0/1). Filtros se aplican por proceso. |
| `en_control_calidad` | `en_control_de_calidad` | Indicador de QC (1=Sí) | Mapeo directo (0/1). Filtros se aplican por proceso. |
| `documento_comercial` | `documento_comercial` | Pedido SAP | Usado para cruce con Visión |
| `posicion_sd` | `posicion_sd` | Posición Pedido | Usado para cruce con Visión |
| `correlativo_int` | (Derivado) | Correlativo numérico | Extraído del primer grupo de dígitos de `lote` |
| `is_test` | (Derivado) | Es prueba/muestra | 1 si `lote` tiene caracteres alfanuméricos |

### 1.2 Reporte Visión Planta (ZPP_VISION)

Representa la cartera de pedidos y su estado de avance.
Cada carga reemplaza completamente los datos anteriores.

**Tabla DB:** `sap_vision_snapshot`

| Campo DB | Columna Excel (Normalizada) | Descripción |
|---|---|---|
| `pedido` | `pedido` | Nro Pedido (PK parcial) |
| `posicion` | `pos` | Posición (PK parcial) |
| `cliente` | `cliente` | Nombre Cliente |
| `cod_material` | `cod_material` | Número de Parte |
| `fecha_de_pedido` | `fecha_de_pedido` | **Fecha comprometida** (Due Date) |
| `solicitado` | `solicitado` | Cantidad total (piezas) |
| `x_fundir` | `x_fundir` | Pendiente de fundición |
| `desmoldeo` | `desmoldeo` | En desmoldeo |
| `tt` | `tt` | En tratamiento térmico |
| `terminacion` | `terminacion` | En terminación |
| `bodega` | `bodega` | En bodega |
| `despachado` | `despachado` | Entregado |
| `peso_neto_ton` | `peso_neto` | Peso total pedido |
| `peso_unitario_ton` | (Derivado) | `peso_neto_ton / solicitado` |

### 1.3 Reporte Desmoldeo (WIP Enfriamiento) - *Por Implementar*

Fuente SAP que informa qué moldes están actualmente en proceso de enfriamiento y cuándo se liberarán las cajas.

**Tabla DB Propuesta:** `sap_demolding_snapshot`

| Campo DB | Columna Excel | Uso |
|---|---|---|
| `material` | `Pieza` | Número de parte |
| `texto_breve` | `Tipo pieza` | Descripción |
| `lote` | `Lote` | Identificador lote |
| `flask_id` | `Caja` | ID físico de la caja |
| `demolding_date` | `Fecha Desmoldeo` | Fecha real liberación caja |
| `demolding_time` | `Hora Desm.` | Hora liberación |
| `mold_type` | `Tipo molde` | Identifica tests |
| `poured_date` | `Fecha fundida` | Fecha vaciado |
| `poured_time` | `Hora Fundida` | Hora vaciado |
| `cooling_hours` | `Hs. Enfria` | Tiempo estimado enfriamiento |
| `mold_quantity` | `Cant. Moldes` | Fracción de molde (0-1) |
| `manufacturing_order` | `OF` | Orden Fabricación (Futuro) |

Campos a ignorar: `Enfriamiento`, `Fecha a desmoldear` (estimado), `Colada`, `UA de Molde`, `Días para entregar`.

---

## 2. Bases de Datos Maestras Internas

### 2.1 Maestro de Materiales (`material_master`)

Tabla gestionada por el usuario (CRUD) para completar datos faltantes en SAP.

| Campo | Tipo | Descripción |
|---|---|---|
| `material` | TEXT (PK) | Número de parte |
| `family_id` | TEXT (FK) | Familia (determina ruta) |
| `aleacion` | TEXT | Aleación (Metalurgia) |
| `flask_size` | TEXT | Tamaño Caja (S/M/L) |
| `piezas_por_molde` | REAL | Rendimiento de caja |
| `tiempo_enfriamiento_molde_dias` | REAL | **Horas** de enfriamiento moldes (planner convierte a días) |
| `finish_days` | INT | **Días** de terminaciones (planner convierte a horas) |
| `min_finish_days` | INT | **Días** mínimos de terminaciones (planner convierte a horas) |
| `vulcanizado_dias` | INT | Lead time Vulcanizado |
| `mecanizado_dias` | INT | Lead time Mecanizado |
| `inspeccion_externa_dias` | INT | Lead time Insp. Externa |
| `peso_unitario_ton` | REAL | Peso por pieza (Neto) |
| `mec_perf_inclinada` | BOOL | Material requiere capacidad especial: mecanizado de perforación inclinada |
| `sobre_medida_mecanizado` | BOOL | Material requiere capacidad especial: sobre medida en mecanizado |

**Nota sobre restricciones:** Las restricciones booleanas (`mec_perf_inclinada`, `sobre_medida_mecanizado`) indican **capacidades especiales requeridas por el material**. En el dispatcher, solo líneas marcadas con estas capacidades pueden procesar materiales que las requieren. Las líneas con capacidades especiales pueden procesar tanto materiales especiales como normales.

### 2.2 Configuración (`app_config`, `process`, `resource`)

Definiciones de la planta.

- **`family_catalog`**: Lista de familias válidas.
- **`process`**: Procesos productivos (ej: "Moldeo", "Terminaciones").
    - Campo `sap_almacen` define el código de almacén asociado.
- **`resource`**: Líneas o máquinas dentro de un proceso.
    - `capacity_per_day`: Capacidad nominal diaria.
- **`resource_constraint`**: Reglas de asignación.
    - Ej: Resource="Línea 1" → Attr="family_id" Rule="EQUALS" Value="Sag Molino".

---

## 3. Tablas Transaccionales (Planificación)

Estas tablas son generadas o derivadas por la aplicación.

### 3.1 SAP Staging
- `sap_mb52_snapshot`: Último MB52 cargado (stock por lote/almacén).
- `sap_vision_snapshot` + vista `sap_vision`: Pedidos Visión Planta (demanda).

### 3.2 Pedidos derivados
- `orders`: Copia derivada de MB52+Visión, por (pedido,posicion,material), usada para prioridades y creación de jobs.
- Prioridades: `orderpos_priority` (pedido+posición) y compatibilidad `order_priority` (solo pedido).

### 3.3 Jobs
- `job`: Orden de trabajo interna (qty, prioridad, cliente, correlativos).
- `job_unit`: Unidades físicas (lote, correlativo) asociadas al job.

### 3.4 Planner (Moldeo)
- Escenario/datos: `planner_scenarios`, `planner_parts`, `planner_orders`, `planner_resources`, `planner_flask_types`, `planner_calendar_workdays`.
- Condiciones iniciales: `planner_initial_order_progress`, `planner_initial_patterns_loaded`, `planner_initial_flask_inuse`, `planner_initial_pour_load`.

### 3.5 Locks / progreso
- `program_in_progress`, `program_in_progress_item`: Bloqueos/pinning de secuencias en curso.
- `last_program`: Último programa generado (por proceso).

### 3.6 Métricas
- `vision_kpi_daily`: KPI diarios reconstruidos desde Visión.
