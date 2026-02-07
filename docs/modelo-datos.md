# Foundry Plan — Modelo de Datos

## 1. Sistema de Códigos de Material

Foundry Plan consolida 4 tipos de materiales (Pieza, Molde, Fundido, Tratamiento Térmico) en un **maestro unificado** basado en `part_code` (5 dígitos).

### 1.1 Estructura de Códigos SAP

Los 4 tipos de materiales comparten el mismo código de parte de 5 dígitos:

| Tipo | Formato SAP (11 dígitos) | Ejemplo | part_code (5 dígitos) |
|------|--------------------------|---------|----------------------|
| **Pieza** | `40XX00YYYYY` | `40330021624` | `21624` |
| **Molde** | `4310YYYYYXX` | `43102162401` | `21624` |
| **Fundido** | `435XX0YYYYY` | `43533021624` | `21624` |
| **Trat. Térmico** | `436XX0YYYYY` | `43633021624` | `21624` |

Donde:
- `XX` = Código de aleación (32=CM2, 33=CM3, 34=CM4, 37=WS170, 38=CMHC, 42=CM6, 21=SP1, 28=SPX)
- `YYYYY` = **part_code** compartido (5 dígitos)

### 1.2 Maestro Consolidado

**Tabla:** `core_material_master`

**Primary Key:** `part_code TEXT PRIMARY KEY` (5 dígitos)

**Estrategia:**
- Un solo registro por `part_code` consolida todos los tipos
- Las tablas transaccionales (orders, mb52, vision) siguen usando códigos completos de 11 dígitos
- Los JOINs extraen `part_code` on-the-fly usando `extract_part_code_sql()`

### 1.3 Funciones de Extracción

**Python:** `src/foundryplan/data/material_codes.py`

```python
def extract_part_code(material: str) -> str:
    """Extrae código de parte de 5 dígitos desde material de 11 dígitos"""
    # Pieza: 40XX00YYYYY -> YYYYY
    # Molde: 4310YYYYYXX -> YYYYY  
    # Fundido: 435XX0YYYYY -> YYYYY
    # Trat.Term: 436XX0YYYYY -> YYYYY
```

**SQL:** `extract_part_code_sql(column)`

Genera expresión CASE para extraer `part_code` en queries:

```sql
-- Ejemplo de JOIN con maestro
SELECT v.*, p.descripcion_pieza, p.family_id
FROM core_sap_vision_snapshot v
LEFT JOIN core_material_master p 
  ON p.part_code = CASE 
    WHEN v.cod_material GLOB '40[0-9][0-9]00[0-9][0-9][0-9][0-9][0-9]' 
      THEN SUBSTR(v.cod_material, 7, 5)
    WHEN v.cod_material GLOB '4310[0-9][0-9][0-9][0-9][0-9][0-9][0-9]' 
      THEN SUBSTR(v.cod_material, 5, 5)
    -- ... más casos ...
    ELSE v.cod_material
  END
```

### 1.4 Catálogo de Aleaciones

**Tabla:** `core_alloy_catalog`

Almacena aleaciones activas con su código de 2 dígitos:

| alloy_code | alloy_name | is_active |
|------------|------------|-----------|
| 32 | CM2 | 1 |
| 33 | CM3 | 1 |
| 34 | CM4 | 1 |
| 37 | WS170 | 1 |
| 38 | CMHC | 1 |
| 42 | CM6 | 1 |
| 21 | SP1 | 1 |
| 28 | SPX | 1 |

**Uso:**
- Filtrado de Vision: solo productos finales con aleación en catálogo activo
- Validación de maestro de materiales

---

## 2. Fuentes de Datos Externas (Excel/SAP)

### 2.1 Reporte Stock MB52

Representa el inventario físico por lote en almacenes seleccionados.
Cada carga reemplaza completamente los datos anteriores ("snapshot").

**Tabla DB:** `core_sap_mb52_snapshot`

| Campo DB | Columna Excel (Normalizada) | Descripción | Mapeo / Regla |
|---|---|---|---|
| `material` | `material` | Número de parte (11 dígitos) | Copia directa |
| `texto_breve` | `texto_breve_de_material` | Descripción | Copia directa |
| `centro` | `centro` | Centro SAP | Copia directa |
| `almacen` | `almacen` | Almacén SAP | Copia directa |
| `lote` | `lote` | Identificador de lote | Copia directa |
| `pb_almacen` | `pb_a_nivel_de_almacen` | Peso bruto (informativo) | Copia directa |
| `libre_utilizacion` | `libre_utilizacion` | Indicador de disponibilidad | Mapeo directo (0/1). Filtros se aplican por proceso. |
| `en_control_calidad` | `en_control_de_calidad` | Indicador de QC (1=Sí) | Mapeo directo (0/1). Filtros se aplican por proceso. |
| `documento_comercial` | `documento_comercial` | Pedido SAP | Usado para cruce con Visión |
| `posicion_sd` | `posicion_sd` | Posición Pedido | Usado para cruce con Visión |
| `material_base` | (Derivado) | Material de pieza final | Mapeado desde Vision usando pedido/posición (para moldes) |
| `correlativo_int` | (Derivado) | Correlativo numérico | Extraído del primer grupo de dígitos de `lote` |
| `is_test` | (Derivado) | Es prueba/muestra | 1 si `lote` tiene caracteres alfanuméricos |

**Filtros de Importación:**
- **Centro**: Solo `centro = sap_centro` (default: "4000")
- **Almacén**: Solo almacenes configurados en procesos activos
- **Material**: Sin filtrado por prefijo (importa todos)

### 2.2 Reporte Visión Planta (ZPP_VISION)

Representa la cartera de pedidos y su estado de avance.
Cada carga reemplaza completamente los datos anteriores.

**Tabla DB:** `core_sap_vision_snapshot`

| Campo DB | Columna Excel (Normalizada) | Descripción |
|---|---|---|
| `pedido` | `pedido` | Nro Pedido (PK parcial) |
| `posicion` | `pos` | Posición (PK parcial) |
| `cliente` | `cliente` | Nombre Cliente |
| `cod_material` | `cod_material` | Número de Parte (11 dígitos) |
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
| `status_comercial` | `estado_comercial` | Estado comercial (solo "activo" se importa) |

**Filtros de Importación:**
- **Aleación**: Solo productos finales (Pieza: `40XX00YYYYY`) con `XX` en catálogo de aleaciones activo
- **Fecha**: `fecha_de_pedido > 2023-12-31`
- **Status**: `status_comercial = 'activo'` (case-insensitive)

### 2.3 Reporte Desmoldeo

Fuente SAP que informa moldes en enfriamiento y desmoldados.
Se divide automáticamente en **dos tablas** según fecha de desmoldeo.

#### A. Moldes por Fundir (WIP)

**Tabla DB:** `core_moldes_por_fundir`

Moldes **sin fecha de desmoldeo** (aún en proceso de enfriamiento).

| Campo DB | Columna Excel | Descripción |
|---|---|---|
| `material` | (Derivado) | **Código de pieza extraído de campo `Pieza`** (11 dígitos) |
| `tipo_pieza` | `Pieza` | Descripción original completa (ej: "MOLDE PIEZA 40330021624") |
| `flask_id` | `Caja` | ID físico completo de la caja |
| `cancha` | `Cancha` | Ubicación física |
| `lote` | `Lote` | Identificador lote |
| `mold_type` | `Tipo molde` | Identifica tests |
| `poured_date` | `Fecha fundida` | Fecha vaciado |
| `poured_time` | `Hora Fundida` | Hora vaciado |
| `cooling_hours` | `Hs. Enfria` | Tiempo estimado enfriamiento (horas) |
| `mold_quantity` | `Cant. Moldes` | **Fracción de caja por pieza** (REAL: 0.25, 0.5, 1.0) |

**Criterio:** Filas **SIN** `Fecha Desmoldeo`

#### B. Piezas Fundidas (Completadas)

**Tabla DB:** `core_piezas_fundidas`

Piezas **con fecha de desmoldeo** (proceso completado, caja liberada).

| Campo DB | Columna Excel | Descripción |
|---|---|---|
| `material` | (Derivado) | **Código de pieza extraído** (11 dígitos) |
| `tipo_pieza` | `Pieza` | Descripción original completa |
| `flask_id` | `Caja` | ID físico completo de la caja |
| `cancha` | `Cancha` | Ubicación física |
| `lote` | `Lote` | Identificador lote |
| `demolding_date` | `Fecha Desmoldeo` | **Fecha real liberación caja** (NOT NULL) |
| `demolding_time` | `Hora Desm.` | Hora liberación |
| `mold_type` | `Tipo molde` | Identifica tests |
| `poured_date` | `Fecha fundida` | Fecha vaciado |
| `poured_time` | `Hora Fundida` | Hora vaciado |
| `cooling_hours` | `Hs. Enfria` | **Tiempo real enfriamiento (horas)** |
| `mold_quantity` | `Cant. Moldes` | **Fracción de caja por pieza** (REAL) |

**Criterio:** Filas **CON** `Fecha Desmoldeo`

#### Filtros de Importación

1. **Campos obligatorios:** `Pieza`, `Caja`, `flask_id`
2. **Cancha:** Solo canchas configuradas (default: TCF-L1000 a T CF-L3000, TDE-D0001 a D0003)
3. **Extracción de material:**
   - Regex: `(\d{11})(?:\D|$)` busca 11 dígitos consecutivos
   - Ejemplo: "MOLDE PIEZA 40330021624" → `material = "40330021624"`
   - Si no encuentra, usa últimos 11 caracteres si son dígitos

#### Actualización Automática

Al importar desmoldeo:

1. **Actualiza `core_material_master`:**
   - `flask_size`: Primeros 3 caracteres de `flask_id` (de ambas tablas)
   - `tiempo_enfriamiento_molde_horas`: `cooling_hours` **SOLO de `piezas_fundidas`** (datos reales)
   - `piezas_por_molde`: `ROUND(1.0 / mold_quantity)` **SOLO de `piezas_fundidas`** (datos reales)
     * Si `mold_quantity = 0.25` → `piezas_por_molde = 4`
     * Si `mold_quantity = 0.5` → `piezas_por_molde = 2`
     * Solo actualiza si `mold_quantity > 0`

2. **Regenera `planner_daily_resources`:**
   - Reconstruye baseline desde config
   - Descuenta flasks ocupadas **de `moldes_por_fundir`** hasta `demolding_date + 1`
   - Usa `ceil(mold_quantity)` para redondeo: 0.75 cajas → 1 caja ocupada

---

## 3. Maestro de Materiales Consolidado

### 3.1 Tabla Principal

**Tabla:** `core_material_master`

**Primary Key:** `part_code TEXT PRIMARY KEY` (5 dígitos, consolida Pieza/Molde/Fundido/TratTerm)

| Campo | Tipo | Descripción | Uso |
|-------|------|-------------|-----|
| `part_code` | TEXT (PK) | Código de parte (5 dígitos) | Clave consolidada |
| `descripcion_pieza` | TEXT | Descripción del material | Display |
| `family_id` | TEXT (FK) | Familia de producto | Determina líneas válidas (dispatcher) |
| `aleacion` | TEXT | Aleación metalúrgica | Planner: agrupación de coladas |
| `flask_size` | TEXT | Tipo de caja (ej: "105", "120", "143") | Planner: restricción de flasks |
| `piezas_por_molde` | REAL | Piezas por molde | Planner: cálculo de moldes necesarios |
| `tiempo_enfriamiento_molde_horas` | INTEGER | **Horas** de enfriamiento | Planner: ventana de ocupación de flask |
| `finish_days` | INTEGER | Días de terminación nominal | Planner: cálculo de completion_day |
| `min_finish_days` | INTEGER | Días mínimos de terminación | Planner: compresión máxima permitida |
| `vulcanizado_dias` | INTEGER | Lead time vulcanizado | Dispatcher: cálculo de start_by |
| `mecanizado_dias` | INTEGER | Lead time mecanizado | Dispatcher: cálculo de start_by |
| `inspeccion_externa_dias` | INTEGER | Lead time inspección | Dispatcher: cálculo de start_by |
| `peso_unitario_ton` | REAL | Peso neto por pieza (toneladas) | Planner: capacidad de colada |
| `mec_perf_inclinada` | INTEGER (0/1) | Requiere perforación inclinada | Dispatcher: restricción de línea |
| `sobre_medida_mecanizado` | INTEGER (0/1) | Requiere sobre medida | Dispatcher: restricción de línea |

**Conversión de tiempos en Planner:**
- `finish_hours = finish_days × 24` (convertido a horas)
- `min_finish_hours = min_finish_days × 24`
- `tiempo_enfriamiento_molde_horas` se usa directamente (ya está en horas)

---

## 4. Recursos Diarios del Planner

### 4.1 Tabla de Recursos Diarios

**Tabla:** `planner_daily_resources`

Almacena capacidades diarias **ya descontadas** por condiciones iniciales.

**Primary Key:** `(scenario_id, day, flask_type)`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `scenario_id` | INTEGER | Escenario de planner |
| `day` | TEXT | Fecha ISO (YYYY-MM-DD) |
| `flask_type` | TEXT | Tipo de caja (ej: "105", "120", "143") |
| `available_qty` | INTEGER | **Cajas disponibles** (Total - Ocupadas) |
| `molding_capacity_per_day` | INTEGER | Capacidad moldeo = molding_per_shift × turnos |
| `same_mold_capacity_per_day` | INTEGER | Capacidad mismo molde = same_mold_per_shift × turnos |
| `pouring_tons_available` | REAL | Toneladas fusión = pour_per_shift × turnos |

### 4.2 Generación en 3 Fases

**Fase 1: Baseline (Config + Turnos + Feriados)**

Ejecutado por: `rebuild_daily_resources_from_config(scenario_id)`

```python
# Horizonte
horizon = min(planner_horizon_days, days_to_last_vision_order)
# Mínimo 30 días, máximo según config

# Solo días laborables
for day in date_range(today, today + horizon):
    if day.weekday() < 5 and day not in holidays:
        # Turnos del día (configurables por día de semana)
        molding_shifts = molding_shifts_json[day_name]
        pour_shifts = pour_shifts_json[day_name]
        
        # Capacidades
        molding_capacity = molding_per_shift × molding_shifts
        same_mold_capacity = same_mold_per_shift × molding_shifts
        pouring_tons = pour_per_shift × pour_shifts
        
        # Flasks totales (sin descuento)
        for flask_type in planner_flask_types:
            available_qty = flask_type.qty_total
```

**Fase 2: Descuento por Desmoldeo**

Ejecutado por: `update_daily_resources_from_demolding(scenario_id)`

```python
# Lee moldes_por_fundir filtrados por cancha
for molde in moldes_por_fundir:
    flask_type = flask_id[:3]  # Primeros 3 chars
    demolding_date = molde.demolding_date or today
    
    # Ocupar flasks desde hoy hasta demolding_date + 1
    for day in range(today, demolding_date + 1):
        UPDATE planner_daily_resources
        SET available_qty = MAX(0, available_qty - CEIL(mold_quantity))
        WHERE day = day AND flask_type = flask_type
```

**Fase 3: Consumo por Schedule**

Ejecutado por: Planner solver durante optimización

El solver decrementa recursos según schedule generado:
- Moldeo → `molding_capacity`
- Same mold → `same_mold_capacity`
- Colada → `pouring_tons_available`
- Flasks → `available_qty[flask_type]`

### 4.3 Triggers de Regeneración

**Automática:**
- Al guardar Config > Planner → regenera baseline + descuento
- Al importar Desmoldeo → regenera baseline + descuento

**Manual:**
- Botón "Regenerar Recursos Diarios" en UI

---

## 5. Configuración del Planner

### 5.1 Recursos Base

**Tabla:** `planner_resources`

| Campo | Tipo | Descripción | Default |
|-------|------|-------------|---------|
| `scenario_id` | INTEGER (PK) | Escenario | 1 |
| `molding_max_per_shift` | INTEGER | Moldes por turno | 10 |
| `molding_max_same_part_per_day` | INTEGER | Máx. mismo material/día | 20 |
| `pour_max_ton_per_shift` | REAL | Toneladas fusión por turno | 5.0 |
| `molding_shifts_json` | TEXT (JSON) | Turnos moldeo por día semana | `{"lun":2,"mar":2,...}` |
| `pour_shifts_json` | TEXT (JSON) | Turnos fusión por día semana | `{"lun":2,"mar":2,...}` |
| `max_placement_search_days` | INTEGER | Máx. días búsqueda placement | 365 |
| `allow_molding_gaps` | INTEGER (0/1) | Permitir huecos en moldeo | 0 |
| `notes` | TEXT | Notas del escenario | NULL |

### 5.2 Tipos de Flask

**Tabla:** `planner_flask_types`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `scenario_id` | INTEGER (PK) | Escenario |
| `flask_type` | TEXT (PK) | Código de caja (ej: "105") |
| `qty_total` | INTEGER | Inventario total de cajas |
| `codes_csv` | TEXT | Códigos alternativos (separados por coma) |
| `label` | TEXT | Etiqueta descriptiva |
| `notes` | TEXT | Notas |

---

## 6. Filtros de Disponibilidad por Proceso

### 6.1 Configuración de Procesos

**Tabla:** `process`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `process_id` | TEXT (PK) | ID del proceso |
| `label` | TEXT | Nombre descriptivo |
| `sap_almacen` | TEXT | Código de almacén SAP |
| `is_active` | INTEGER (0/1) | Proceso activo |
| `is_special_moldeo` | INTEGER (0/1) | Es proceso de moldeo (Planner) |
| `availability_predicate_json` | TEXT (JSON) | Filtros de disponibilidad |

### 6.2 Predicado de Disponibilidad

**Formato JSON:**

```json
{
  "libre_utilizacion": <0|1|null>,
  "en_control_calidad": <0|1|null>
}
```

**Reglas:**
- Valor 0 o 1 → filtra por ese valor exacto
- `null` o ausente → no filtra por ese campo
- Campos presentes se combinan con AND

**Ejemplos:**

| Proceso | Predicado | SQL Generado | Caso de Uso |
|---------|-----------|--------------|-------------|
| Te rminaciones | `{"libre_utilizacion": 1, "en_control_calidad": 0}` | `WHERE libre_utilizacion=1 AND en_control_calidad=0` | Stock disponible |
| Toma dureza | `{"libre_utilizacion": 0, "en_control_calidad": 1}` | `WHERE libre_utilizacion=0 AND en_control_calidad=1` | Stock bloqueado/QC |
| Mecanizado | `{"libre_utilizacion": 1}` | `WHERE libre_utilizacion=1` | Solo libre, ignora QC |
| Custom | `{}` o `null` | `WHERE 1=1` | Sin filtros |

---

## 7. Tablas de Salida del Planner

### 7.1 Schedule de Moldes

**Tabla:** `planner_schedule`

Resultado de la planificación (moldes por orden y día).

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `scenario_id` | INTEGER | Escenario |
| `order_id` | TEXT | ID de orden |
| `day` | TEXT | Fecha ISO |
| `qty_molds` | INTEGER | Cantidad de moldes a moldear |

### 7.2 Métricas por Orden

**Devuelto por solver (no persistido):**

```python
{
    "molds_schedule": {order_id: {day_idx: qty_molds}},
    "pour_days": {order_id: [day_idx, ...]},
    "shakeout_days": {order_id: day_idx},
    "completion_days": {order_id: day_idx},
    "finish_hours": {order_id: hours_effective},
    "late_days": {order_id: days_late},
    "errors": ["Order X: reason", ...],
    "status": "HEURISTIC" | "HEURISTIC_INCOMPLETE"
}
```

---

## 8. Normalización de Claves SAP

### 8.1 Funciones de Normalización

**Python:** `_normalize_sap_key(value)`

Convierte texto/numérico a string normalizado para comparaciones:

```python
# Entrada: "000123.0" o 123.0 o "123"
# Salida: "123"
```

**Uso:**
- Comparar `documento_comercial` entre MB52 y Vision
- Comparar `posicion_sd` entre MB52 y Vision

### 8.2 Mapeo Material Base

Durante importación de MB52, se mapea `material_base`:
- Lee Vision para obtener material de pieza desde pedido/posición
- Asigna a MB52 cuando almacén contiene código de molde
- Permite rastrear moldes hasta pieza final

---

## Resumen de Nomenclatura

### Prefijos de Tablas

- **`core_`**: Datos compartidos (SAP snapshots, maestro, config)
- **`dispatcher_`**: Datos del módulo Dispatcher
- **`planner_`**: Datos del módulo Planner

### Convenciones de Campos

- **`*_id`**: Claves primarias o foráneas (TEXT)
- **`*_json`**: Campos de configuración JSON (TEXT)
- **`*_days`**: Tiempos en días calendario (INTEGER)
- **`*_hours`**: Tiempos en horas (INTEGER/REAL)
- **`*_ton`**: Pesos en toneladas (REAL)
- **`is_*`**: Flags booleanos (INTEGER: 0 o 1)
