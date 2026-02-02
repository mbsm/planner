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
| `tiempo_enfriamiento_molde_dias` | INT | Días de enfriamiento moldes |
| `vulcanizado_dias` | INT | Lead time Vulcanizado |
| `mecanizado_dias` | INT | Lead time Mecanizado |
| `inspeccion_externa_dias` | INT | Lead time Insp. Externa |
| `peso_unitario_ton` | REAL | Peso por pieza (Neto) |
| `mec_perf_inclinada` | BOOL | Restricción técnica |
| `sobre_medida_mecanizado` | BOOL | Restricción técnica |

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

Estas tablas son generadas por la aplicación.

### 3.1 Jobs (`job`, `job_unit`)
La unidad fundamental de trabajo.
- `job`: Representa una orden de trabajo para una cantidad de piezas (`qty`) de un `material` para un `pedido`.
- `job_unit`: Representa cada unidad física (serializada por `lote` y `correlativo`).

### 3.2 Dispatch Output (`dispatch_queue_*`)
Salida del algoritmo de despacho (Scheduler V2).
- `dispatch_queue_run`: Cabecera de una ejecución de dispatch.
- `dispatch_queue_item`: Lista secuenciada de trabajos asignados a recursos.
    - `seq`: Número de secuencia (1 = Primero).
    - `pinned`: Si el trabajo fue fijado manualmente por el usuario.

### 3.3 Planner Output (Moldeo)
Salida del optimizador OR-Tools.
- `planner_scenarios`: Escenarios de simulación.
- `planner_parts`: Copia local de atributos de parte para el solver.
- `planner_orders`: Copia local de demanda para el solver.
- `plan_daily_order`: Resultado (Cantidad a moldear por día/orden).
