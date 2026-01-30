# Foundry Plan — Modelo de datos

Este documento detalla el schema de base de datos (tablas SQLite), mapeo desde fuentes SAP, y reglas de transformación.

## 1) Fuentes de datos (SAP)
### 1.1 MB52 (stock por unidad/lote)

**Estrategia de persistencia**: Última versión (reemplazo total)
- Se guarda **siempre la última versión** del archivo.
- Siempre reemplaza la tabla completa (sin modo merge).
- **snapshot_id** y **loaded_at** son auditoría interna.

**Campos esperados en Excel (nombre SAP directo / nombre interno normalizado)**:

| Campo | Tipo | Descripción |
|---|---|---|
| `Material` / `material` | texto | Número de parte SAP |
| `Texto Breve de Material` / `texto_breve` | texto | Descripción corta |
| `Centro` / `centro` | texto/num | Centro SAP (normalizado a texto) |
| `Almacén` / `almacen` | texto/num | Almacén SAP (normalizado a texto) |
| `PB a nivel de almacén` / `pb_almacen` | real | Peso bruto a nivel almacén |
| `Lote` / `lote` | texto | Correlativo/lote físico (alfanumérico) |
| `Libre utilización` / `libre_utilizacion` | 0/1 | Flag: 1=utilizable, 0=no utilizable |
| `En control de calidad` / `en_control_calidad` | 0/1 | Flag: 1=en QC, 0=no en QC |
| `Documento Comercial` / `documento_comercial` | texto/num | Número de pedido SAP |
| `Posición (SD)` / `posicion_sd` | texto/num | Posición de pedido SAP |

**Filtros aplicados en import**:
1. **Material**: Filtrado por `sap_material_prefixes` (config: CSV o `*` para todos).
2. **Centro**: Debe coincidir con `sap_center` (config).
3. **Almacén**: Se carga; luego se filtra por `process.sap_almacen` en tiempo de consulta.
4. **Requeridos**: Material, Centro, Almacén, Lote, Documento Comercial, Posición SD, Libre utilización, En control calidad.
5. **Derivados**: `pb_almacen` (si existe), `correlativo_int`, `is_test`.

**Campos derivados en la tabla interna**:
- `correlativo_int`: Primer grupo numérico del `lote` (para orden/agrupación).
- `is_test`: 1 si `lote` contiene caracteres alfanuméricos.

### 1.2 Visión Planta (pedido/posición + atributos)

**Estrategia de persistencia**: Última versión (reemplazo total)
- Se guarda **siempre la última versión** del archivo.
- Reemplaza totalmente la tabla anterior.
- **snapshot_id** y **loaded_at** son auditoría interna.

**Campos esperados en Excel (nombre SAP directo / nombre interno normalizado)**:

| Campo | Tipo | Descripción | Categoría |
|---|---|---|---|
| `Pedido` / `pedido` | texto | Número de pedido | **Cruce** |
| `Pos.` / `posicion` | texto | Posición de pedido | **Cruce** |
| `Tip. Pos` / `tipo_posicion` | texto | Tipo de posición SAP | Información |
| `Tipo de reparto` / `tipo_de_reparto` | texto | Tipo de reparto | Información |
| `Cliente` / `cliente` | texto | Nombre del cliente | Información |
| `N° OC Cliente` / `n_oc_cliente` | texto | OC del cliente | Información |
| `Pos.OC` / `pos_oc` | texto | Posición en OC cliente | Información |
| `Material Client Code` / `material` | texto | Material "Client Code" SAP | Información |
| `Cod. Material` / `cod_material` | texto | Código de material SAP | Información |
| `Descripción Material` / `descripcion_material` | texto | Descripción de material | Información |
| `Atributo` / `atributo` | texto | Atributo adicional | Información |
| `Fecha de pedido` / `fecha_de_pedido` | date | **Fecha comprometida** con cliente (cuándo espera recibir) | **Planificación** |
| `Fecha Entrega` / `fecha_entrega` | date | Estimación interna SAP de entrega (opcional, NO usar en general) | Información |
| `Solicitado` / `solicitado` | entero | Cantidad total pedida (piezas) | **Información** |
| `X Programar` / `x_programar` | entero | Por programar (piezas) | Progreso |
| `Programado` / `programado` | entero | Programado (piezas) | Progreso |
| `X Fundir` / `x_fundir` | entero | Por fundir (piezas) | **Progreso/Moldeo** |
| `Desmoldeo` / `desmoldeo` | entero | En desmoldeo (piezas) | Progreso |
| `TT` / `tt` | entero | En tratamiento térmico (piezas) | Progreso |
| `Terminación` / `terminacion` | entero | En terminación (piezas) | Progreso |
| `Bodega` / `bodega` | entero | En bodega/almacén (piezas) | Progreso |
| `Despachado` / `despachado` | entero | Despachado (piezas) | Progreso |
| `Peso Neto` / `peso_neto_ton` | real | Peso neto total del pedido (tons) | Información |
| `Rechazo` / `rechazo` | entero | Rechazos (piezas) | Control |
| `Ret. QM` / `ret_qm` | entero | Retención QM (piezas) | Control |
| `Grupo Art` / `grupo_art` | texto | Grupo de artículo | Información |
| `Proveedor` / `proveedor` | texto | Proveedor | Información |
| `Status` / `status` | texto | Status actual | Información |
| `Jerarquía productos` / `jerarquia_productos` | texto | Jerarquía de productos | Información |
| `Status Comercial` / `status_comercial` | texto | Status comercial | Información |
| `En Vulcaniz.` / `en_vulcaniz` | entero | En vulcanizado (piezas) | Progreso |
| `Pend. Vulcanizado` / `pend_vulcanizado` | entero | Pendiente vulcanizado (piezas) | Progreso |
| `Rech. Insp. Externa` / `rech_insp_externa` | entero | Rechazos inspección externa | Control |
| `Insp. Externa` / `insp_externa` | entero | En inspección externa (piezas) | Progreso |
| `Lib. Vulcaniz.(DE)` / `lib_vulcaniz_de` | entero | Librados vulcanizado (DE) (piezas) | Progreso |
| `Mecanizado Interno` / `mecanizado_interno` | entero | Mecanizado interno (piezas) | Progreso |
| `Mecanizado Externo` / `mecanizado_externo` | entero | Mecanizado externo (piezas) | Progreso |

**Requeridos para cruce y planificación**:
- `pedido`, `posicion`, `fecha_de_pedido` (fecha comprometida con cliente)

**Notas sobre fechas**:
- `fecha_de_pedido`: **Fecha comprometida** con el cliente; es la correcta para calcular `start_by` y planificación.
- `fecha_entrega`: Opcional; es una estimación interna SAP de nuestra entrega. **No usar en planificación general**.

**Notas sobre cantidad y peso**:
- `solicitado`: Cantidad total del pedido (piezas); es información base, NO progreso.
- `peso_neto_ton`: Peso neto total del pedido (tons).
- `peso_unitario_ton` (derivado en DB): `peso_neto_ton / solicitado` = peso por pieza (tons).

**Filtros y normalizaciones aplicadas en import**:
1. **Encabezados**: Se normalizan variantes comunes (ej: `pos` → `posicion`, `fecha_pedido` → `fecha_de_pedido`).
2. **Columnas alias por campo de progreso**: Se buscan aliases conocidas (ej: `x_fundir`, `porfundir`, `fundir` → `x_fundir`).
3. **Conversión**: Pesos se normalizan a tons (kg → tons); fechas se parsean desde Excel.

**Importante - Todas las cantidades en piezas**:
- **TODOS los campos de avance/progreso** (`solicitado`, `x_programar`, `programado`, `desmoldeo`, `tt`, `terminacion`, `bodega`, `despachado`, **`x_fundir`**) están en **unidades (piezas)**.
- El stock de moldes en MB52 está en **moldes** (cantidad física de moldes).
- Para calcular **avance de moldeo**, convertir `x_fundir` (piezas) a moldes: `x_fundir_moldes = x_fundir / piezas_por_molde`.

## 2) Schema SQLite (tablas internas)
### 2.1 Configuración

**app_config** (key/value)

| Campo | Tipo | Uso |
|---|---|---|
| `config_key` | texto (PK) | Nombre del parámetro (ej: `plant_name`, `sap_material_prefixes`) |
| `config_value` | texto | Valor (simple o JSON para listas/objetos) |
| `updated_at` | datetime | Auditoría |

Parámetros típicos:
- `plant_name`, `sap_center`
- `sap_material_prefixes` (CSV o JSON)
- `process_warehouse_map` (JSON: {process_id: almacen})
- `ui_flags` (JSON)
- `job_priority_map` (JSON: {"prueba": 1, "urgente": 2, "normal": 3})

**process** (procesos configurables)

| Campo | Tipo | Uso |
|---|---|---|
| `process_id` | texto (PK) | Clave del proceso (ej: `moldeo`, `terminaciones`) |
| `label` | texto | Nombre visible |
| `sap_almacen` | texto | Almacén SAP asociado (puede haber múltiples por proceso) |
| `is_active` | 0/1 | Habilitado |
| `is_special_moldeo` | 0/1 | Solo `moldeo=1` |
| `availability_predicate_json` | texto | Predicado configurable para stock usable (ej: `{"libre_utilizacion": 1, "en_control_calidad": 0}` o `{"en_control_calidad": 1}`) |

**process_attribute_def** (atributos de restricción por proceso)

| Campo | Tipo | Uso |
|---|---|---|
| `process_id` | texto (FK) | Proceso |
| `attr_key` | texto | Nombre atributo (ej: `familia`, `sobre_medida`) |
| `attr_type` | texto | `bool` \| `enum` \| `number_range` |
| `allowed_values_json` | texto | JSON para `enum` |
| `min_value` | real | Para `number_range` |
| `max_value` | real | Para `number_range` |
| `is_required` | 0/1 | Si aplica como filtro obligatorio |

**resource** (líneas/recursos por proceso)

| Campo | Tipo | Uso |
|---|---|---|
| `resource_id` | texto (PK) | Identificador de línea |
| `process_id` | texto (FK) | Proceso |
| `name` | texto | Nombre visible |
| `capacity_per_day` | real | Capacidad (si aplica) |
| `sort_order` | entero | Orden en UI |
| `is_active` | 0/1 | Habilitado |

**resource_constraint** (restricciones por línea)

| Campo | Tipo | Uso |
|---|---|---|
| `resource_id` | texto (FK) | Línea |
| `attr_key` | texto | Atributo (de `process_attribute_def`) |
| `rule_type` | texto | `bool` \| `enum` \| `number_range` |
| `rule_value_json` | texto | JSON para valores permitidos |

### 2.2 Maestro local

**family_catalog**

| Campo | Tipo | Uso |
|---|---|---|
| `family_id` | texto (PK) | Clave familia |
| `label` | texto | Nombre visible |
| `is_active` | 0/1 | Habilitada |

**material_master**

Tabla de maestro de materiales: datos clave por número de parte (material).

| Campo | Tipo | Descripción | Origen | Uso |
|---|---|---|---|---|
| `material` | texto (PK) | Número de parte SAP | MB52 directo | Identificador único |
| `family_id` | texto (FK) | Familia del material | Entrada manual | Restricciones por línea |
| `aleacion` | texto | Tipo de aleación | Entrada manual | Información; planificador moldeo |
| `piezas_por_molde` | real | Conversión piezas ↔ moldes | Entrada manual | Cálculo avance moldeo; conversión en informes |
| `peso_bruto_ton` | real | Peso bruto por pieza (tons) | Entrada manual | Información; planificador moldeo |
| `tiempo_enfriamiento_molde_dias` | entero | Tiempo de enfriamiento del molde (días) | Entrada manual | Planificador moldeo |
| `vulcanizado_dias` | entero | Lead time vulcanizado | Entrada manual | Cálculo `start_by`; planificación |
| `mecanizado_dias` | entero | Lead time mecanizado | Entrada manual | Cálculo `start_by`; planificación |
| `inspeccion_externa_dias` | entero | Lead time inspección externa | Entrada manual | Cálculo `start_by`; planificación |
| `peso_unitario_ton` | real | Peso neto por pieza (tons) | Derivado: `peso_neto_ton_pedido / solicitado` (Visión) | Información; reportes |
| `mec_perf_inclinada` | 0/1 | Atributo: perforación inclinada | Entrada manual | Restricción por línea (Mecanizado) |
| `sobre_medida_mecanizado` | 0/1 | Atributo: sobre medida mecanizado | Entrada manual | Restricción por línea |
| `created_at` | datetime | Fecha de creación | Auto | Auditoría |
| `updated_at` | datetime | Última modificación | Auto | Auditoría |

**Notas sobre sincronización**:
- Cada vez que aparece un material en **MB52** o **Visión Planta** que NO está en `material_master` → popup solicita:
  - `family_id` (Familia)
  - `aleacion` (Tipo de aleación)
  - `piezas_por_molde` (Conversión moldeo)
  - `peso_bruto_ton` (Peso bruto por pieza)
  - `tiempo_enfriamiento_molde_dias` (Enfriamiento molde)
  - `vulcanizado_dias`, `mecanizado_dias`, `inspeccion_externa_dias` (Lead times)
  - `mec_perf_inclinada`, `sobre_medida_mecanizado` (Atributos)
- `peso_unitario_ton` se calcula automático desde Visión Planta: `peso_neto_ton_pedido / solicitado` (se actualiza cada vez que se carga Visión).
- Si cambia `peso_unitario_ton` respecto al valor anterior, se solicita al usuario **actualizar `peso_bruto_ton`** (confirmación de nuevo peso bruto).
- Cambios en tiempos (`vulcanizado_dias`, `mecanizado_dias`, `inspeccion_externa_dias`) invalidan Plan (afecta `start_by`).
- Cambios en `family_id`, `mec_perf_inclinada`, `sobre_medida_mecanizado` invalidan Programa (afecta restricciones por línea).
- Cambios en `aleacion`, `piezas_por_molde`, `peso_bruto_ton`, `tiempo_enfriamiento_molde_dias` invalidan Plan (afectan planificador moldeo).

### 2.3 Staging SAP (snapshots)

**Aclaración sobre "snapshots"**: No son históricos, sino **última versión cargada** desde SAP.
- Las tablas `sap_mb52_snapshot` y `sap_vision_snapshot` guardan **solo los últimos datos importados**.
- Los campos `snapshot_id` y `loaded_at` son **auditoría interna** (pueden ignorarse en consultas), no para reproducibilidad histórica.
- Si se requiere histórico en el futuro, se crearía una tabla separada `sap_mb52_history` con timestamp de cambios.
- SAP es la fuente de verdad: si un pedido se **despacha**, desaparece de Visión; si termina un proceso, **desaparece del almacén** de ese proceso y aparece en el siguiente.
- La aplicación **refleja** estos cambios mediante reemplazo total y **recalcula** jobs/colas en cada carga.

**sap_mb52_snapshot**

| Campo | Tipo | Uso |
|---|---|---|
| `snapshot_id` | entero (PK) | Lote de carga |
| `loaded_at` | datetime | Timestamp de carga |
| `texto_breve` | texto | Descripción corta (SAP) |
| `centro` | texto | Centro SAP |
| `almacen` | texto | Almacén SAP |
| `pb_almacen` | real | Peso bruto a nivel almacén (SAP) |
| `lote` | texto | Correlativo/lote |
| `libre_utilizacion` | 0/1 | Flag SAP (stock utilizable) |
| `en_control_calidad` | 0/1 | Flag SAP (stock en QC) |
| `documento_comercial` | texto | Pedido SAP |
| `posicion_sd` | texto | Posición SAP |
| `correlativo_int` | entero | Derivado del lote (primer grupo numérico) |
| `is_test` | 0/1 | Derivado: 1 si lote contiene letras lote |
| `is_test` | 0/1 | Derivado (alfanumérico) |

Notas de uso:
- Se carga **una fila por unidad física** (una pieza/molde).
- Los campos `material`, `texto_breve`, `centro`, `almacen`, `pb_almacen`, `lote`, `libre_utilizacion`, `en_control_calidad`, `documento_comercial`, `posicion_sd` son **mapeo directo 1 a 1 desde SAP**.
- La determinación de **stock usable** por proceso es configurable vía `process.availability_predicate_json`:
  - Ej Terminaciones: `{"libre_utilizacion": 1, "en_control_calidad": 0}` (stock que puede fluir).
  - Ej Toma de Dureza: `{"almacen": "<almacen_term>", "en_control_calidad": 1}` (solo QC, mismo almacén).
  - El predicado se aplica **en tiempo de consulta** al filtrar stock por proceso.
- El stock de **moldes no fundidos** se obtiene filtrando por almacén configurado para Moldeo y aplicando su predicado.

Descripción de columnas:
- `snapshot_id`: Identificador del batch de carga (permite reproducibilidad y trazabilidad).
- `loaded_at`: Timestamp de importación.
- `material`: Número de parte SAP (mapeo directo).
- `texto_breve`: Descripción corta de material SAP (mapeo directo; UI).
- `centro`: Centro SAP (mapeo directo; normalizado a texto).
- `almacen`: Almacén SAP (mapeo directo; se usa para filtrar stock por proceso).
- `pb_almacen`: Peso bruto a nivel almacén SAP (mapeo directo; unidad SAP).
- `lote`: Correlativo/lote físico (mapeo directo; puede ser alfanumérico).
- `libre_utilizacion`: Flag SAP (mapeo directo; 0/1 indica stock utilizable).
- `en_control_calidad`: Flag SAP (mapeo directo; 0/1 indica stock en QC).
- `documento_comercial`: Número de pedido SAP (mapeo directo; cruza con Visión.pedido).
- `posicion_sd`: Posición de pedido SAP (mapeo directo; cruza con Visión.posicion).
- `correlativo_int`: Derivado; primer grupo de dígitos del `lote` (ordenamiento).
- `is_test`: Derivado; 1 si `lote` contiene caracteres alfanuméricos.

**sap_vision_snapshot**

| Campo | Tipo | Uso |
|---|---|---|
| `snapshot_id` | entero (PK) | Lote de carga |
**sap_vision_snapshot**

Campos en tabla (en orden de importancia/uso):

| Campo | Tipo | Uso |
|---|---|---|
| `snapshot_id` | entero (PK) | Lote de carga |
| `loaded_at` | datetime | Timestamp de carga |
| `pedido` | texto | Pedido (cruce con MB52.documento_comercial) |
| `posicion` | texto | Posición (cruce con MB52.posicion_sd) |
| `fecha_de_pedido` | date | Base de planificación |
| `fecha_entrega` | date | Fecha de compromiso |
| `solicitado` | entero | Cantidad total (piezas) |
| `x_fundir` | entero | Por fundir (piezas) |
| `x_programar` | entero | Por programar (piezas) |
| `programado` | entero | Programado (piezas) |
| `desmoldeo` | entero | En desmoldeo (piezas) |
| `tt` | entero | En TT (piezas) |
| `terminacion` | entero | En terminación (piezas) |
| `bodega` | entero | En bodega (piezas) |
| `despachado` | entero | Despachado (piezas) |
| `peso_neto_ton` | real | Peso neto (tons) |
| `cliente` | texto | Nombre cliente |
| `n_oc_cliente` | texto | OC cliente |
| `cod_material` | texto | Código de material SAP |
| `descripcion_material` | texto | Descripción de material |
| `tipo_posicion` | texto | Tipo de posición |
| `tipo_de_reparto` | texto | Tipo de reparto |
| `pos_oc` | texto | Posición en OC cliente |
| `material` | texto | Material "Client Code" |
| `atributo` | texto | Atributo adicional |
| `rechazo` | entero | Rechazos (piezas) |
| `ret_qm` | entero | Retención QM (piezas) |
| `grupo_art` | texto | Grupo artículo |
| `proveedor` | texto | Proveedor |
| `status` | texto | Status actual |
| `status_comercial` | texto | Status comercial |
| `en_vulcaniz` | entero | En vulcanizado (piezas) |
| `pend_vulcanizado` | entero | Pendiente vulcanizado (piezas) |
| `rech_insp_externa` | entero | Rechazos inspección externa |
| `insp_externa` | entero | En inspección externa (piezas) |
| `lib_vulcaniz_de` | entero | Librados vulcanizado (DE) (piezas) |
| `mecanizado_interno` | entero | Mecanizado interno (piezas) |
| `mecanizado_externo` | entero | Mecanizado externo (piezas) |

Notas de uso:
- **TODOS los campos de progreso están en piezas**, incluyendo `x_fundir`.
- **Requeridos para cruce/planificación**: `pedido`, `posicion`, `fecha_de_pedido`.
- **Progreso (todos en piezas)**: `solicitado`, `x_programar`, `programado`, `desmoldeo`, `tt`, `terminacion`, `bodega`, `despachado`, `x_fundir`.
- **Auditoría/control**: `rechazo`, `ret_qm`, `rech_insp_externa`, `status`, `status_comercial`.
- **Información de pedido**: `tipo_posicion`, `tipo_de_reparto`, `cliente`, `n_oc_cliente`, `pos_oc`, `grupo_art`, `proveedor`.
- **Material/producto**: `cod_material`, `descripcion_material`, `material`, `atributo`.
- Rechazos: SAP ajusta `x_fundir` automáticamente, reflejándose sin lógica adicional.

Descripción resumida de campos clave:
- `pedido`, `posicion`: Cruce con MB52.
- `fecha_de_pedido`: Base de planificación (start_by calculations).
- `fecha_entrega`: Fecha de compromiso.
- `solicitado`: Cantidad total pedida (piezas).
- `x_fundir`: Por fundir (piezas); se compara con MB52 de moldes (requiere conversión).
- Campos de progreso (`x_programar`, `programado`, `desmoldeo`, `tt`, `terminacion`, `bodega`, `despachado`): Piezas en cada etapa.
- `peso_neto_ton`: Peso total en tons.
- Campos de información: `cliente`, `n_oc_cliente`, `cod_material`, `descripcion_material`, etc.

### 2.4 Jobs y dispatch

#### 2.4.1 job

Tabla de órdenes de trabajo por proceso. Cada job agrupa un pedido/posición/material en un proceso, con prioridad y estado.

| Campo | Tipo | Descripción |
|---|---|---|
| `job_id` | texto (PK) | ID único interno (ej: `job_20260130_001`) |
| `process_id` | texto (FK) | ID del proceso (ej: `mecanizado`, `terminacion`) |
| `pedido` | texto | Pedido SAP (cruza con MB52/Visión) |
| `posicion` | texto | Posición SAP (cruza con MB52/Visión) |
| `material` | texto (FK) | Número de parte (FK a `material_master`) |
| `qty_total` | real | Cantidad total (piezas o moldes según proceso) |
| `qty_completed` | real | Completado (auditoría; puede venir de Visión) |
| `qty_remaining` | real | Pendiente: `qty_total - qty_completed` |
| `priority` | entero | Prioridad numérica (menor = mayor prioridad) |
| `is_test` | 0/1 | 1 si derivado de lotes alfanuméricos (prueba) |
| `state` | texto | `pending` \| `in_process` |
| `fecha_entrega` | date | Desde Visión (información de negocio) |
| `notes` | texto | Observaciones operacionales |
| `created_at` | datetime | Auditoría |
| `updated_at` | datetime | Auditoría |
| `completed_at` | datetime | Auditoría (cuando pasó a `completed`) |

**Notas sobre sincronización**:
- Jobs se crean **al importar MB52** para cada proceso configurado:
  - 1 job por (pedido, posición, material, proceso)
  - Por defecto `state='pending'`
  - `priority` se inicializa con valor "normal" (ej: 3) desde `job_priority_map` config
  - Las **pruebas** (lotes alfanuméricos) se marcan `is_test=1` y usan prioridad "prueba" (ej: 1)
- El usuario puede **splittear** un job desde la GUI en múltiples jobs (mismo pedido/posición/proceso, distintos `job_id`):
  - Los splits se crean y mantienen **antes del scheduler**
  - El scheduler actúa solo sobre jobs (no crea splits)
  - Los splits los dispara el usuario desde la GUI, salvo en pruebas (automático)
- El usuario marca **urgentes** desde la GUI (no automático):
  - Cambiar `priority` a valor "urgente" (ej: 2) desde `job_priority_map`
  - "normal" es el valor por defecto (ej: 3)
- Los **splits** y pines operativos (`dispatch_in_progress`) se mantienen al recalcular colas (datos persistentes).
- Los **splits** se actualizan solo desde MB52 (stock real); si pedido/posición desaparece de Visión, no se modifican splits.
- Cuando entra nuevo stock con splits existentes, el sistema asigna al split con menor cantidad actual.
- Si splits quedaron en cero y luego llega stock nuevo, se crea un solo job (splits anteriores no se reutilizan).
- Si material no existe en `material_master` → popup solicita campos antes de crear job.
- `qty_total` se calcula desde MB52 (stock real por almacén del proceso).
- `qty_completed` se actualiza desde Visión (progreso) cada vez que se carga Visión Planta.
- Cambios en Visión (fechas, progreso) no invalidan jobs existentes, solo actualizan cantidades.
- Si pedido/posición desaparece de Visión, job se cierra (histórico; no se regenera).
- Si pedido/posición desaparece del almacén del proceso (MB52), job queda sin stock y se cierra.
- Si reaparecen unidades, job puede reabrirse o se crea uno nuevo (según estado previo).

#### 2.4.2 job_unit

Tabla de lotes concretos (unidades físicas) dentro de cada job. Vincula cada lote/correlativo con el job y su cantidad.

| Campo | Tipo | Descripción |
|---|---|---|
| `job_unit_id` | texto (PK) | ID único (ej: `ju_20260130_001`) |
| `job_id` | texto (FK) | Job al que pertenece |
| `lote` | texto | Lote/correlativo físico (desde MB52) |
| `correlativo_int` | entero | Primer grupo numérico del lote (para orden) |
| `qty` | real | Cantidad de este lote |
| `status` | texto | `available` \| `reserved` \| `in_progress` \| `completed` \| `on_hold` |
| `created_at` | datetime | Auditoría |
| `updated_at` | datetime | Auditoría |

**Notas sobre sincronización**:
- Un `job_unit` se crea por cada lote único en MB52 que forme parte del job.
- El `status` es informativo (auditoria); el estado real viene del `job.state`.
- Si un lote se descarta o desaparece en SAP → marcar como `on_hold`, no borrar (trazabilidad).

#### 2.4.3 dispatch_queue_run

Tabla de ejecuciones/corridas del algoritmo de dispatch. Cada corrida genera una cola de trabajo por línea/recurso.

| Campo | Tipo | Descripción |
|---|---|---|
| `run_id` | texto (PK) | ID único de corrida (ej: `run_20260130_mecanizado_001`) |
| `process_id` | texto (FK) | Proceso para el cual se corrió el dispatcher |
| `generated_at` | datetime | Timestamp de generación |
| `source_mb52_snapshot_id` | entero | Snapshot de MB52 usado |
| `source_vision_snapshot_id` | entero | Snapshot de Visión usado |
| `algo_version` | texto | Versión del algoritmo (auditoría) |
| `notes` | texto | Observaciones de la corrida |

**Notas sobre sincronización**:
- Se crea una nueva `run` **automáticamente** al cargar MB52 (y cuando cambian Config/recursos si se fuerza recalcular colas).
- Permite trazabilidad: ver qué snapshot/versión de algoritmo generó cada cola.

#### 2.4.4 dispatch_queue_item

Tabla de ítems en cola (orden de ejecución por línea/recurso).

| Campo | Tipo | Descripción |
|---|---|---|
| `run_id` | texto (FK) | Corrida a la que pertenece |
| `resource_id` | texto (FK) | ID de línea/recurso (ej: `linea_mecanizado_01`) |
| `seq` | entero | Posición en la cola (1, 2, 3, ...) |
| `job_id` | texto (FK) | Job asignado |
| `qty` | real | Cantidad asignada a esta línea |
| `pinned` | 0/1 | 1 si está siendo procesado (no se debe reordenar) |
| `eta_start` | datetime | Estimación de inicio (calculada) |
| `eta_end` | datetime | Estimación de fin (calculada) |
| `notes` | texto | Observaciones (ej: "Atraso estimado 2 horas") |
| `created_at` | datetime | Auditoría |

**Notas sobre sincronización**:
- La cola se ordena por: `pinned=1` primero (en progreso), luego `priority` ascendente, luego `start_by` ascendente.
- El algoritmo elige la línea con menor carga actual.
- Los ETA se calculan basados en cantidad, lead times desde `material_master`, y tiempo de setup (configurable).
#### 2.4.4.1 dispatch_error

Tabla de errores asociados a una corrida de dispatch (diagnóstico por proceso).

| Campo | Tipo | Descripción |
|---|---|---|
| `run_id` | texto (FK) | Corrida a la que pertenece |
| `process_id` | texto (FK) | Proceso |
| `pedido` | texto | Pedido SAP (si aplica) |
| `posicion` | texto | Posición SAP (si aplica) |
| `material` | texto | Material SAP (si aplica) |
| `error_code` | texto | Código (ej: `MB52_SIN_VISION`) |
| `message` | texto | Descripción legible |
| `created_at` | datetime | Auditoría |

**Regla clave**:
- Si existe un `pedido/posicion` en **MB52** que **no** existe en **Visión Planta**, se registra un error en esta tabla para la corrida de dispatch.

#### 2.4.5 last_dispatch

Tabla de "últimas colas guardadas" por proceso. Permite mantener una cola activa incluso después de generar nuevas.

| Campo | Tipo | Descripción |
|---|---|---|
| `process_id` | texto (PK) | Proceso |
| `run_id` | texto (FK) | ID de la última corrida guardada |
| `saved_at` | datetime | Timestamp de guardado |

**Notas sobre sincronización**:
- Se actualiza cuando el usuario presiona "Guardar" en la página de dispatch.
- Permite revertir a la última cola guardada sin regenerar desde cero.

#### 2.4.6 dispatch_in_progress

Tabla de sesión de progreso por proceso. Representa el "estado vivo" mientras se ejecuta una cola.

| Campo | Tipo | Descripción |
|---|---|---|
| `in_progress_id` | texto (PK) | ID único de sesión (ej: `dip_20260130_mecanizado`) |
| `process_id` | texto (FK) | Proceso |
| `from_run_id` | texto (FK) | Corrida de origen (referencial; auditoría) |
| `started_at` | datetime | Cuándo empezó esta ejecución |
| `updated_at` | datetime | Última actualización |
| `notes` | texto | Observaciones generales de la sesión |

**Notas sobre sincronización**:
- Se crea cuando el usuario inicia la ejecución de la cola vigente (desde `last_dispatch`).
- Se cierra cuando todas las líneas reportan que terminaron o se presiona "Cerrar sesión".
- Un job marcado **en proceso** no debe cambiar de línea cuando se recalcula el dispatch y debe permanecer en las **primeras posiciones** de su línea.
- Puede haber **más de un job en proceso** por línea.
- El movimiento manual de jobs entre líneas es permitido **solo si la configuración lo habilita**.

#### 2.4.7 dispatch_in_progress_item

Tabla de progreso en vivo por línea/recurso dentro de una sesión.

| Campo | Tipo | Descripción |
|---|---|---|
| `in_progress_id` | texto (FK) | Sesión a la que pertenece |
| `resource_id` | texto (FK) | Línea/recurso |
| `seq` | entero | Posición actual en la cola |
| `job_id` | texto (FK) | Job siendo ejecutado |
| `qty_target` | real | Cantidad objetivo para este job en esta línea |
| `qty_completed` | real | Completado hasta ahora |
| `correlativos_json` | texto | JSON: lista de lotes/segmentos completados (auditoría) |
| `started_at` | datetime | Cuándo comenzó este ítem |
| `estimated_end` | datetime | ETA de fin (basada en lead times + avance actual) |
| `actual_end` | datetime | Fin real (cuando se completa o se descarta) |
| `notes` | texto | Observaciones específicas de esta línea |
| `updated_at` | datetime | Auditoría |

**Notas sobre sincronización**:
- Cada `dispatch_in_progress_item` es un snapshot del progreso real en vivo.
- `correlativos_json` almacena: `[{lote: "001-002", qty: 50}, {lote: "001-003", qty: 30}]` (para auditoría y trazabilidad).
- Se actualiza cuando el usuario reporta avance desde la UI (botón "Reportar completado").
- Cuando se completa la cantidad objetivo, automáticamente se cierra y se abre el siguiente ítem en la cola de esa línea.

### 2.5 Plan semanal (Planificador Moldeo)

#### 2.5.1 weekly_plan_run

Tabla de corridas del planificador semanal. Genera un plan de cuántos moldes/piezas fundir cada semana para cada pedido/posición.

| Campo | Tipo | Descripción |
|---|---|---|
| `plan_id` | texto (PK) | ID único de plan (ej: `plan_20260203_w01`) |
| `week_start` | date | Fecha de inicio de la semana (lunes, ISO) |
| `generated_at` | datetime | Timestamp de generación |
| `source_mb52_snapshot_id` | entero | Snapshot de MB52 usado (stock moldes) |
| `source_vision_snapshot_id` | entero | Snapshot de Visión usado (demanda/progreso) |
| `algo_version` | texto | Versión del algoritmo (auditoría) |
| `notes` | texto | Observaciones generales del plan |

**Notas sobre sincronización**:
- Se crea cada vez que el usuario presiona "Generar Plan Semanal" en la página Plan.
- El planificador calcula para cada pedido/posición:
  1. Cuántas piezas se necesitan fundir esta semana (`qty_planned_piezas`).
  2. Cuántos moldes necesitan fundirse (`qty_planned_moldes = qty_planned_piezas / piezas_por_molde`).
  3. Cuál es el stock de moldes no fundidos disponible (desde MB52 almacén moldeo).
  4. Si hay moldes disponibles: reservarlos e incluirlos en el plan.

#### 2.5.2 weekly_plan_item

Tabla de ítems en el plan semanal (por pedido/posición). Contiene la decisión semanal de cuánto fundir.

| Campo | Tipo | Descripción |
|---|---|---|
| `plan_id` | texto (FK) | Corrida a la que pertenece |
| `pedido` | texto | Pedido SAP |
| `posicion` | texto | Posición SAP |
| `material` | texto (FK) | Número de parte (FK a `material_master`) |
| `qty_solicitado_piezas` | real | Cantidad total solicitada (desde Visión) |
| `qty_pendiente_piezas` | real | Pendiente: `qty_solicitado - progreso_actual` |
| `qty_planned_piezas` | real | **Decisión del plan**: piezas a fundir esta semana |
| `qty_planned_moldes` | real | Moldes equivalentes: `qty_planned_piezas / piezas_por_molde` |
| `stock_moldes_disponibles` | real | Stock de moldes no fundidos (desde MB52) |
| `moldes_reservados` | real | Moldes "reservados" para este pedido en esta semana (≤ `stock_moldes_disponibles`) |
| `target_start_week` | date | Semana objetivo de inicio (basada en `start_by` del job) |
| `fecha_entrega` | date | Fecha de entrega (desde Visión; información) |
| `manual_override` | 0/1 | 1 si el usuario cambió manualmente `qty_planned_piezas` |
| `confidence_pct` | real | % de confianza en poder cumplir (auditoría; 0-100) |
| `notes` | texto | Observaciones específicas de este ítem |
| `created_at` | datetime | Auditoría |

**Notas sobre sincronización**:
- Un `weekly_plan_item` se crea por cada pedido/posición con stock moldeo disponible O demanda pendiente.
- La lógica de planificación considera:
  - **Urgencia**: pedidos con `fecha_entrega` próxima se planifican primero.
  - **Disponibilidad**: si hay moldes en stock moldeo, se reservan.
  - **Capacidad**: si hay restricción de capacidad moldeo (configurable), reducir `qty_planned_moldes`.
  - **Lead times**: considerar `tiempo_enfriamiento_molde_dias` para no comprometer futuras semanas.
- Si el usuario cambia `qty_planned_piezas` manualmente → `manual_override=1` (auditoría).
- `confidence_pct` puede ser usado para alertar (ej: <70% = alerta de riesgo).

### 2.5.3 weekly_plan_simulation (opcional, derivada)

Tabla temporal (o vista) para simular el plan actual y predecir avance moldeo futuro.

| Campo | Tipo | Descripción |
|---|---|---|
| `plan_id` | texto | ID del plan |
| `week` | date | Semana |
| `pedido` | texto | Pedido |
| `posicion` | texto | Posición |
| `material` | texto | Parte |
| `stock_moldes_inicio_semana` | real | Stock al inicio (moldes) |
| `qty_fundir_piezas` | real | Cantidad a fundir esta semana (piezas) |
| `qty_fundir_moldes` | real | Equivalente en moldes |
| `tiempo_enfriamiento_dias` | real | Tiempo enfriamiento molde (desde maestro) |
| `moldes_listos_siguiente_semana` | real | Stock predicho para semana siguiente |
| `piezas_moldeadas_estimadas` | real | Usando fórmula avance moldeo |
| `avance_pct_estimado` | real | % avance predicho |

**Notas**:
- Esta tabla es **temporal** durante la simulación, pero puede persistirse para auditoría.
- Permite responder: "¿Si hacemos este plan, en qué semana cumpliremos la fecha de entrega?"
- Sirve para validar viabilidad antes de guardar el plan.

### 2.5.4 Vistas derivadas (no persistentes)

#### moldeo_progress (avance por pedido/posición)

Vista derivada que calcula el estado actual de moldeo para cada pedido/posición. Se usa en la página Plan para mostrar avance y en el planificador para decisiones.

| Campo | Tipo | Cálculo / Origen |
|---|---|---|
| `pedido` | texto | Desde Visión |
| `posicion` | texto | Desde Visión |
| `material` | texto | Desde Visión |
| `cantidad_pedido_piezas` | real | Visión: `solicitado` |
| `por_fundir_piezas` | real | Visión: `x_fundir` |
| `por_fundir_moldes` | real | Derivado: `x_fundir / piezas_por_molde` |
| `stock_moldes_no_fundidos` | real | MB52 almacén moldeo: filtrar moldes usables |
| `piezas_por_molde` | real | `material_master.piezas_por_molde` |
| `piezas_moldeadas` | real | **Fórmula central**: `solicitado - x_fundir + (stock_moldes_no_fundidos × piezas_por_molde)` |
| `avance_pct` | real | `(piezas_moldeadas / cantidad_pedido_piezas) × 100` |
| `fecha_entrega` | date | Visión: fecha de compromiso |
| `dias_para_entrega` | entero | Hoy - fecha_entrega (negativo si futuro) |

**Notas de cálculo**:
- Visión reporta **TODO en piezas**: `solicitado`, `x_fundir`, todos los campos de progreso.
- MB52 reporta **moldes** (no piezas) en el almacén de moldeo.
- La conversión piezas ↔ moldes requiere `piezas_por_molde` (entrada manual en maestro).
- **Fórmula explicada**:
  - `piezas_moldeadas = solicitado - x_fundir + (stock_moldes_no_fundidos × piezas_por_molde)`
  - `solicitado`: total pedido
  - `- x_fundir`: menos lo aún por fundir
  - `+ (stock_moldes_no_fundidos × piezas_por_molde)`: más lo que se puede fundir desde moldes en stock
- **Rechazos**: SAP ajusta automáticamente `x_fundir` (piezas); no requiere lógica adicional.
- Fallos en fundición (piezas rechazadas): SAP refleja como aumento en `x_fundir`.
- Esta vista es **crucial** para la Page Plan y para validar viabilidad de planes.

#### dispatch_status_summary (resumen de colas por proceso)

Vista derivada que suma el estado de todas las colas activas (para dashboard/Home).

| Campo | Tipo | Cálculo |
|---|---|---|
| `process_id` | texto | ID del proceso |
| `total_jobs_pending` | entero | COUNT donde `job.state='pending'` |
| `total_jobs_in_process` | entero | COUNT donde `job.state='in_process'` |
| `qty_total_pending` | real | SUM de `qty_remaining` donde `state='pending'` |
| `qty_total_in_process` | real | SUM de `qty_remaining` donde `state='in_process'` |
| `current_run_id` | texto | `last_dispatch.run_id` |
| `lines_active` | entero | COUNT DISTINCT `resource_id` donde `dispatch_in_progress_item` está activo |
| `estimated_completion_time` | datetime | MAX de `eta_end` en `dispatch_queue_item` actual |

#### job_priority_calculated (prioridad calculada)

Vista derivada que ayuda al UI a mostrar orden de ejecución sugerido.

| Campo | Tipo | Cálculo |
|---|---|---|
| `job_id` | texto | ID del job |
| `state` | texto | Desde `job` (`pending` \| `in_process`) |
| `is_test` | 0/1 | Desde `job` |
| `priority` | entero | Desde `job` (menor = mayor prioridad; prueba=1, urgente=2, normal=3) |
| `start_by` | date | Calculado: `fecha_entrega - (vulcanizado_dias + mecanizado_dias + inspeccion_externa_dias)` |
| `dias_para_start_by` | entero | Hoy - `start_by` (negativo = futuro; positivo = retrasado) |
| `sort_key` | real | Fórmula de ordenamiento: `priority` ascendente, luego `start_by` ascendente |

### 2.6 Resumen de relaciones (ER conceptual)

```
material_master (referencia central de materiales)
├── ← job (un job por pedido/posición/material/proceso)
│   ├── ← job_unit (lotes que conforman el job)
│   └── ← dispatch_queue_item (asignaciones en colas)
│       ├── ← dispatch_queue_run (corrida del dispatcher)
│       └── ← dispatch_in_progress_item (progreso en vivo)
│           └── ← dispatch_in_progress (sesión de ejecución)

weekly_plan_run (corrida semanal)
├── ← weekly_plan_item (decisiones por pedido/posición)
│   └── → material_master (consulta `piezas_por_molde`)
└── ← weekly_plan_simulation (predicción de avance)

sap_mb52_snapshot / sap_vision_snapshot (auditoría/trazabilidad)
└── referenciados por dispatch_queue_run / weekly_plan_run
```

### 2.7 Naming y equivalencias (as-built → futuro)
- `last_program` → `last_dispatch`
- `program_in_progress` / `program_in_progress_item` → `dispatch_in_progress` / `dispatch_in_progress_item`
- `orderpos_priority` → `job.priority` (numérico configurable)
- `parts` → `material_master` (migración: añadir 6 campos nuevos)

## 3) Mapeo SAP → modelo interno

### 3.1 Flujo de importación: MB52 + Visión Planta → Jobs + Orders

**Paso 1: Cargar MB52**
- Usuario sube archivo MB52 en `/actualizar`.
- App normaliza encabezados, filtra por:
  - `sap_material_prefixes` (config)
  - `sap_center` (config)
  - Se guarda TODO en `sap_mb52_snapshot` (sin filtrar por almacén aún)
- **Se crean jobs automáticamente** para cada proceso configurado:
  - Por cada (pedido/posición/material) en MB52 que coincide con un almacén de proceso configurado.
  - Se crea 1 job con `state='pending'` por cada proceso.
  - Si material NO existe en `material_master` → popup solicita los campos.
  - Se crean `job_unit` por cada lote del MB52.

**Paso 2: Cargar Visión Planta**
- Usuario sube archivo Visión Planta.
- App normaliza encabezados y guarda TODO en `sap_vision_snapshot`.
- **Actualizar jobs existentes** (creados en Paso 1):
  - Por cada fila en Visión: buscar jobs por (pedido, posicion).
  - Actualizar `qty_completed` y `fecha_entrega` desde Visión.
  - Recalcular `qty_remaining`.
  - Si material NO existe en `material_master` → popup solicita campos (antes de crear job).

**Paso 3: Popup material_master**
- Si material es nuevo, popup pide:
  1. `family_id` (Familia)
  2. `aleacion` (Tipo de aleación)
  3. `piezas_por_molde` (Conversión)
  4. `peso_bruto_ton` (Peso bruto)
  5. `tiempo_enfriamiento_molde_dias` (Enfriamiento)
  6. `vulcanizado_dias` (Lead time)
  7. `mecanizado_dias` + `inspeccion_externa_dias` (otros lead times)
  8. `mec_perf_inclinada` (Atributo)
  9. `sobre_medida_mecanizado` (Atributo)
- Usuario completa, se inserta en `material_master`.
- Se prosigue con creación de job.

**Paso 4: Actualizar Jobs (ya creados en Paso 1)**
- Para cada job existente:
  - Actualizar `qty_completed` desde Visión (si existe cruce).
  - Recalcular `qty_remaining`.
  - Si `qty_remaining` llega a 0, job puede marcarse como completado (cierre).
- Guardar cambios con `updated_at` para auditoría.

### 3.2 Invalidación de datos derivados

**Cuándo invalida qué**:

| Evento | Invalida |
|---|---|
| Actualizar campos de tiempo en `material_master` (`vulcanizado_dias`, `mecanizado_dias`, `inspeccion_externa_dias`) | `weekly_plan_run` + `dispatch_queue_run` (afecta `start_by`) |
| Actualizar `family_id` o atributos (`mec_perf_inclinada`, `sobre_medida_mecanizado`) | `dispatch_queue_run` (revalidar restricciones por línea) |
| Actualizar moldeo-específico (`aleacion`, `piezas_por_molde`, `peso_bruto_ton`, `tiempo_enfriamiento_molde_dias`) | `weekly_plan_run` (afecta cálculo moldeo) |
| Cargar nueva Visión Planta | Recalcular `moldeo_progress` (vista), actualizar `qty_completed` en jobs |
| Cargar nuevo MB52 | Crear/actualizar jobs para todos los procesos configurados, recalcular `stock_moldes_no_fundidos` → recalcular `moldeo_progress` |
| Usuario guarda una cola (`last_dispatch`) | Permite revert; no invalida jobs |
| Usuario guarda un plan (`weekly_plan_run`) | No invalida jobs; es una decisión guardada |

**Estrategia de regeneración**:
- Mantener `last_dispatch` y `last_weekly_plan_run` con margen de reutilización (usuario puede revert).
- Si datos se invalidan, marcar como "obsoleto" pero no borrar (trazabilidad).
- UI muestra alerta: "El plan anterior es obsoleto; genera uno nuevo".

### 3.3 Normalización de columnas Excel

Foundry Plan normaliza automáticamente encabezados para buscar:
- Espacios → `_`
- Acentos → caracteres sin acento (ej: `Posición` → `posicion`)
- Mayúsculas → minúsculas

Esto permite que usuario pueda subir Excel con variantes (ej: `Pos.` / `Posición` / `pos`) y la app las mapee al campo esperado.

## 4) Apéndice: Formato Excel esperado

Foundry Plan importa **2 archivos Excel** en `.xlsx` leyendo **solo la primera hoja**:
2. **Visión Planta** (pedido/posición, fechas y pesos)

La app normaliza encabezados a un formato interno (minúsculas, sin acentos, espacios→`_`). Por eso, abajo se listan los nombres **internos** esperados; el archivo puede tener variantes (p. ej. `Pos.` / `Posición`), mientras el normalizador los deje equivalentes.

### A.1) MB52 (stock)

Columnas requeridas (internas):

| Columna | Tipo | Ejemplo | Notas |
|---|---:|---|---|
| `material` | texto | `43633021531` | Número de parte |
| `centro` | texto/num | `4000` | Se normaliza si Excel lo convierte a `4000.0` |
| `almacen` | texto/num | `4035` | Ídem |
| `lote` | texto | `001-002` / `0030PD0674` | Lote/correlativo por pieza; puede ser alfanumérico |
| `libre_utilizacion` | 0/1 | `1` | Usable=1 |
| `en_control_calidad` | 0/1 | `0` | Usable=0 |
| `documento_comercial` | texto/num | `1010044531` | Pedido de venta (SAP) |
| `posicion_sd` | texto/num | `10` | Posición (SAP) |

Columnas opcionales útiles:
- `texto_breve_de_material` o `texto_breve`

Reglas clave:
- Se consideran piezas "usables" cuando se cumple `libre_utilizacion=1` y `en_control_calidad=0`.
- Lotes alfanuméricos (contienen letras) se consideran **pruebas** y se priorizan.
- El correlativo numérico se obtiene desde el **prefijo numérico** del lote (primer grupo de dígitos).

### A.2) Visión Planta

Columnas requeridas (internas):

| Columna | Tipo | Ejemplo | Notas |
|---|---:|---|---|
| `pedido` | texto/num | `1010044531` | Debe cruzar con MB52 `documento_comercial` |
| `posicion` | texto/num | `10` | Debe cruzar con MB52 `posicion_sd` |
| `cod_material` | texto | `43633021531` | Referencial (la orden se arma desde MB52) |
| `fecha_de_pedido` | fecha | `2026-01-20` | Fecha base usada para planificar (se parsea desde Excel) |

Columnas opcionales (mejoran KPI y UI):

| Columna | Tipo | Ejemplo | Uso |
|---|---:|---|---|
| `fecha_entrega` | fecha | `2026-02-10` | Para cards Home (atrasados / próximas 2 semanas) |
| `solicitado` | entero | `120` | Para calcular pendientes vs bodega/despachado |
| `bodega` | entero | `10` | Progreso (pendientes = solicitado - bodega - despachado) |
| `despachado` | entero | `20` | Progreso |
| `peso_neto` | número | `12500` | Viene en **kg**; la app lo guarda en **tons** (kg/1000) |
| `cliente` | texto | `ACME` | UI |
| `n_oc_cliente` | texto | `OC-123` | UI |
| `descripcion_material` | texto | `PARRILLA ...` | UI |

Notas:
- La app calcula y guarda `peso_unitario_ton` como `peso_neto_ton / solicitado` cuando ambas existen.
- Si `fecha_entrega` no está, los cards de Home (atrasados / próximas 2 semanas) no se podrán poblar.
