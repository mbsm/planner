# Plan de Implementación - PlannerTerm v0.2

**Objetivo:** Sincronizar implementación con documentación en `docs/`

**Versión:** v0.2 según especificacion.md y modelo-datos.md

**Última actualización:** 30 de enero de 2026

---

## 📋 RESUMEN EJECUTIVO

Este documento define **qué implementar** basado en la documentación oficial.

- Documentación de referencia: `docs/especificacion.md` + `docs/modelo-datos.md`
- Código a actualizar: `src/foundryplan/`
- Seguimiento: Marcar items con ✅ cuando se completen

---

## 1️⃣ FASE 1: TABLAS & PERSISTENCIA ✅ **COMPLETADO** (Commit: e7fbd74)

### 1.1 Tablas de Configuración Base
- [x] `app_config` - Parámetros de planta (centro, almacenes, prefijos, etc.)
- [x] `family_catalog` - Catálogo de familias
- [x] `material_master` - Maestro de materiales (familia, tiempos, peso, flags)
- [x] `process` - Catálogo de procesos con almacén asociado
- [x] `resource` - Líneas/recursos por proceso
- [x] `resource_constraint` - Restricciones de familia por línea

**Status actual:**
- [x] Validar que existen todas en DB
- [x] Validar estructura matches `modelo-datos.md` sección 5.1
- [x] Seeds: 5 familias, 7 procesos, config SAP
- [x] **✅ Sin backward compatibility:** Solo tablas v0.2, eliminadas tablas legacy

---

### 1.2 Tablas SAP Staging
- [x] `sap_mb52_snapshot` - Filas de MB52 (unidad física + timestamp)
- [x] `sap_vision_snapshot` - Filas de Visión (pedido/pos + atributos + timestamp)

**Status actual:**
- [x] Validar columnas vs especificacion.md
- [x] Tablas creadas con estructura completa
- [x] **✅ Eliminadas tablas legacy** (sap_mb52, sap_vision) - solo v0.2
- [x] **✅ Columnas v0.2:** fecha_de_pedido, peso_neto_ton, peso_unitario_ton, etc.

---

### 1.3 Tablas de Jobs (Core)
- [x] `job` - Entidad core (process, pedido, posicion, material, job_id, priority, is_test, state)
- [x] `job_unit` - Mapeo job_id ↔ lotes concretos

**Requerimientos de modelo-datos.md:**
- [x] `is_test = 1` para lotes alfanuméricos (detectados automáticamente)
- [x] `is_test = 1` NO se puede desmarcar (protegido)
- [x] `priority` numérico (menor = más prioritario)
- [x] Heredar prioridad de pedido/posicion, SALVO tests usan prioridad "prueba"
- [x] Tests persisten a través de recálculos

---

### 1.4 Tablas de Dispatch
- [x] `dispatch_queue_run` - Ejecuciones del dispatcher (run_id, process_id, generated_at, algo_version)
- [x] `dispatch_queue_item` - Ítems en cola (run_id, resource_id, seq, job_id, qty, pinned)
- [x] `last_dispatch` - Último dispatch guardado (para UI, permite revert)
- [x] `dispatch_in_progress` - Sesión de ejecución en vivo
- [x] `dispatch_in_progress_item` - Progreso por línea dentro de sesión

**Requerimientos especificacion.md:**
- [x] Generación automática de queue al cargar MB52 (tabla lista)
- [x] Campo `pinned` = 1 cuando job está "en proceso"
- [x] Jobs pinned se quedan en misma línea en recálculos (schema ready)
- [x] Jobs pinned flotan a TOP de su línea (schema ready)

---

### 1.5 Tablas de Estado Operativo
- [x] `program_in_progress` - Legacy (backward-compat)
- [x] `program_in_progress_item` - Items pinned por (pedido, posicion, line_id)

**Requerimientos:**
- [x] `split_id` field para splits (split_id=1,2,etc)
- [x] `marked_at` timestamp para ordenar locks por antigüedad
- [x] `line_id` para fijar a línea

---

### 1.6 Tablas de Auditoría & KPI
- [x] `vision_kpi_daily` - Snapshots diarios de KPIs
  - Campos: `snapshot_date (PK)`, `snapshot_at`, `tons_por_entregar`, `tons_atrasadas`
  - Propósito: Gráfico histórico de atrasos en Home/Pedidos

**Requerimientos especificacion.md línea 19:**
- [x] "Gráfico histórico de KPI (toneladas atrasadas desde Visión Planta)" - tabla creada
- [x] Cálculo: suma de piezas pendientes * peso_ton para pendientes con fecha_entrega < hoy (pendiente implementar método)
- [x] Persistencia: diaria (upsert por snapshot_date) - schema ready

**Tests:** 7/7 pasando en `tests/test_db_schema.py`

---

## 2️⃣ FASE 2: IMPORTACIÓN SAP ✅ **COMPLETADO** (Commit: e7fbd74)

### 2.1 MB52 Import (`import_sap_mb52_bytes`) ✅
- [x] Validar columnas: material, centro, almacen, lote, libre_utilizacion, documento_comercial, posicion_sd, en_control_calidad
- [x] Normalizar claves SAP (Excel convierte "000010" → 10.0 → normalizar a "10")
- [x] Filtrar por `sap_material_prefixes` configurado
- [x] Aplicar `_mb52_availability_predicate_sql` según proceso:
  - [x] Default: `libre_utilizacion=1 AND en_control_calidad=0`
  - [x] `toma_de_dureza`: `libre_utilizacion=0 OR en_control_calidad=1` (inverso)

**Requerimientos modelo-datos.md:**
- [x] Soportar `mode="merge"` (actualizar solo algunos centro/almacen pairs)
- [x] Soportar `mode="replace"` (limpiar tabla e insertar)
- [x] Al completar import: invalidar `orders` + `last_program` para recalcular
- [x] **✅ Solo sap_mb52_snapshot:** Eliminada lógica dual-insert (simplificado)
- [x] Auto-detección de tests: `is_test = 1 if _is_lote_test(lote) else 0`
- [x] **✅ Nombres v0.2:** material (no numero_parte), correlativo_int, pb_almacen

---

### 2.2 Visión Planta Import (`import_sap_vision_bytes`) ✅
- [x] Validar columnas mínimas: pedido, posicion, cod_material, fecha_de_pedido
- [x] Normalizar columnas con aliases (fecha_de_pedido, tipo_posicion, status_comercial, etc.)
- [x] Convertir `peso_neto` de kg → toneladas (peso_neto_ton)
- [x] Calcular `peso_unitario_ton = peso_neto_tons / solicitado`
- [x] Auto-actualizar `material_master.peso_unitario_ton` desde snapshot
- [x] **✅ Solo sap_vision_snapshot:** Eliminada lógica dual-insert
- [x] **✅ Columnas v0.2:** fecha_de_pedido, terminacion, x_fundir, peso_neto_ton

**Requerimientos especificacion.md línea 97:**
- [ ] "peso_unitario_ton se actualiza desde Visión; si cambia, se solicita actualizar peso_bruto_ton"
- ⚠️ **Nota:** Según User: esto está ya implementado y documentado en peso_neto

**Requerimientos especificacion.md línea 29:**
- [ ] "Si existe pedido/posicion en MB52 que no existe en Visión, se registra en errores de dispatch"
- [ ] Crear entry en diagnostics (opcional: tabla `dispatch_error` futura)

---

### 2.3 Rebuild Orders from SAP ✅
- [x] Método: `rebuild_orders_from_sap_for(process)`
- [x] Lógica: join MB52 + Visión por (documento_comercial, posicion_sd) = (pedido, posicion)
- [x] Agrupa por (pedido, posicion, material), suma lotes

**Detección automática de tests (especificacion.md línea 96):**
- [x] Buscar lotes con alfanuméricos (regex: `re.search(r"[A-Za-z]", lote_s)`)
- [x] Crear `orderpos_priority` con `kind='test'`, `is_priority=1` automáticamente
- [x] ✅ **Implementado:** Tests NO se pueden desmarcar (`delete_all_pedido_priorities(keep_tests=True)`)

**Estado:** Implementación completa con test end-to-end validando:
- Auto-detección de lotes alfanuméricos como tests
- Creación automática de orderpos_priority con kind='test'
- **✅ Sin backward compatibility:** 100% alineado con modelo-datos.md
- **✅ Nombres v0.2:** material, family_id, peso_unitario_ton, fecha_de_pedido

**Archivos modificados:**
- `src/foundryplan/data/db.py`: Eliminadas migraciones legacy, solo v0.2
- `src/foundryplan/data/repository.py`: 189 inserciones, 362 eliminaciones (simplificado)
- `tests/test_db_schema.py`: Test actualizado para usar snapshot tables

**Tests:** 7/7 pasando en `tests/test_db_schema.py` (incluye `test_auto_test_detection_in_rebuild_orders`)
**Commits:** b47dc8d (FASE 1), e2769d7 (FASE 2.3), e7fbd74 (sin backward compat

**Tests:** 8/8 pasando en `tests/test_db_schema.py` (incluye `test_auto_test_detection_in_rebuild_orders`)

---

## 3️⃣ FASE 3: CÁLCULO DE JOBS

### 3.1 Job Creation & Lifecycle ✅ **COMPLETADO** (Commit: 9eec78f)

**🔑 Trigger:** Jobs se crean **automáticamente al importar MB52** (no al cargar Visión)

**📋 Reglas de creación:**
- [x] En `import_sap_mb52_bytes`: después de guardar snapshot, crear jobs automáticamente
- [x] Crear 1 job por (process_id, pedido, posicion, material) para **cada proceso configurado** (no solo terminaciones)
  - [x] Iterar sobre `process` table donde `is_active=1`
  - [x] Filtrar MB52 por `almacen` del proceso (usar `process.sap_almacen`)
  - [x] Agrupar por (pedido, posicion, material)
  - [x] Crear job con `state='pending'`, `qty_total` = COUNT de lotes en MB52
- [x] Si material NO existe en `material_master` → popup solicita campos antes de crear job (futuro)

**📊 Campos iniciales del job:**
- [x] `job_id` = generar único (formato: `job_{process}_{timestamp}_{uuid8}`)
- [x] `process_id` = ID del proceso
- [x] `pedido`, `posicion`, `material` = desde MB52
- [x] `qty_total` = COUNT de lotes actuales en MB52 (representa cantidad **disponible**, no completada)
- [x] `priority` = valor "normal" desde `job_priority_map` config (ej: 3)
- [x] `is_test` = 1 si algún lote es alfanumérico (automático)
- [x] `state` = 'pending' (inicial)
- [x] `fecha_entrega` = NULL (se actualiza al cargar Visión)
- [x] `created_at` = now

**🧪 Prioridad automática para tests:**
- [x] Si `is_test=1` → usar prioridad "prueba" (ej: 1) desde `job_priority_map`
- [x] Tests mantienen prioridad "prueba" siempre (no cambia a "normal")

**🔗 ¿Cómo sabe un job qué lotes tiene asociados?**
- [x] A través de la tabla `job_unit` (relación 1:N - un job tiene muchos lotes)
- [x] Consulta: `SELECT lote, correlativo_int, qty FROM job_unit WHERE job_id = ?`
- [x] Sincronización automática al importar MB52:
  1. `DELETE FROM job_unit WHERE job_id = ?` (elimina lotes anteriores)
  2. `INSERT INTO job_unit` por cada lote actual en MB52
- [x] Los lotes **desaparecen automáticamente** cuando se eliminan del MB52 (import con reemplazo total)

**🔄 Actualización desde Visión Planta:**
- [x] En `import_sap_vision_bytes`: después de guardar snapshot, actualizar jobs existentes
- [x] Buscar jobs por (pedido, posicion)
- [x] Actualizar **solo `fecha_entrega`** desde Visión (no progreso)
- [x] `qty_total` viene solo del MB52 (no se toca desde Visión)

**💡 Lógica de progreso:**
- [x] Los lotes desaparecen del MB52 cuando se completan físicamente
- [x] Por lo tanto, `qty_total` representa la cantidad **actual** de lotes disponibles/pendientes
- [x] No necesitamos `qty_completed` ni `qty_remaining` separados

**🔒 Lifecycle (estado del job):**
- [x] `state='pending'` → job creado, sin iniciar
- [ ] `state='in_process'` → job siendo ejecutado (marcado desde GUI/dispatch)
- [x] Si pedido/pos desaparece de Visión → job persiste (histórico)
- [x] Si pedido/pos desaparece del almacén del proceso (MB52) → `qty_total` baja (lotes se eliminan de job_unit)
- [x] Si todos los lotes desaparecen del MB52 → `qty_total=0` (job persiste pero sin lotes)
- [ ] Si reaparece stock → job puede reabrirse o crear nuevo (según lógica de reactivación)

**📦 Job Units:**
- [x] Crear `job_unit` por cada lote en MB52 del job:
  - [x] `job_unit_id` = generar único (formato: `ju_{job_id}_{uuid8}`)
  - [x] `job_id` = FK al job
  - [x] `lote` = lote físico desde MB52
  - [x] `correlativo_int` = primer grupo numérico del lote
  - [x] `qty` = 1 (una pieza por lote en MB52)
  - [x] `status` = 'available' (inicial)

**Status actual:**
- ✅ Implementación completa de creación automática de jobs desde MB52
- ✅ Método `_create_jobs_from_mb52()` creado y llamado al final de import
- ✅ Método `_update_jobs_from_vision()` creado y llamado al final de Visión import
- ✅ Método `_is_lote_test()` para detectar lotes alfanuméricos
- ✅ Schema job simplificado: eliminado `qty_completed` y `qty_remaining` (innecesarios)
- ✅ Schema job actualizado: `material` (no numero_parte), `fecha_entrega`, `notes`
- ✅ Schema job_unit actualizado: `job_unit_id` PK, `correlativo_int`, `qty`, `status`
- ✅ Sincronización job ↔ job_unit: DELETE + INSERT en cada import MB52
- ✅ Tests: 4 nuevos tests en test_job_creation.py
  - test_create_jobs_from_mb52_basic ✅
  - test_create_jobs_test_priority ✅
  - test_create_jobs_multiple_processes ✅
  - test_update_jobs_from_vision ✅
- ✅ Tests totales: 16/16 pasando

**Archivos modificados:**
- src/foundryplan/data/db.py: Schema job y job_unit actualizados
- src/foundryplan/data/repository.py: Métodos _create_jobs_from_mb52, _update_jobs_from_vision, _is_lote_test
- tests/test_db_schema.py: Actualizado test_job_structure
- tests/test_job_creation.py: 4 nuevos tests (NUEVO)
- docs/modelo-datos.md: Documentada relación job → job_unit y sincronización

**Commits:** ee03efb (docs), 747359a (implementación inicial), 9eec78f (simplificación qty_*), 0ee7cb9 (docs actualizadas)

---

### 3.2 Split Management ✅ **COMPLETADO** - 8/8 tests passing

**🎯 Cuándo se crean splits:** Usuario dispara desde GUI, **ANTES del scheduler** (el scheduler actúa solo sobre jobs)

**📋 Reglas de splits:**
- [x] Método: `split_job(job_id, qty_split)` → crea 2 jobs desde 1 original
  - [x] Job original conserva primeros `qty_split` lotes
  - [x] Nuevo job recibe lotes restantes (`qty_total - qty_split`)
  - [x] Ambos jobs tienen mismo (pedido, posicion, material, process_id)
  - [x] Ambos heredan `priority`, `is_test`, `state`, `fecha_entrega`, `notes`
  - [x] Job units se redistribuyen: primeros N quedan en job1, resto van a job2
  - [x] Validaciones: qty_split > 0 y < qty_total
- [x] Splits persisten en tabla `job` (no en tabla separada)
- [x] Identificar splits por: mismo (pedido, posicion, material, process_id) con múltiples `job_id`

**🔄 Distribución de nuevo stock con splits existentes (modelo-datos.md):**
- [x] Cuando llega nuevo stock de un pedido/posición con splits existentes:
  - [x] Asignar nuevas unidades al split con **menor qty_total actual**
  - [x] Query: ORDER BY qty_total ASC, toma el primero
  - [x] Actualizar `qty_total` del job correspondiente
  - [x] Crear `job_unit` asociados al job correcto
- [x] Si ambos splits quedan en `qty_total=0` y luego llega stock nuevo:
  - [x] Crear **1 solo job nuevo** (no reutilizar splits vacíos)
  - [x] Lógica: if all(qty_total == 0): create new job

**✅ Sincronización qty_total en mode="replace":**
- [x] Track jobs actualizados durante import (set de job_ids)
- [x] Al final del procesamiento por proceso:
  - [x] Jobs NO actualizados → qty_total=0, DELETE job_units
  - [x] Jobs actualizados → mantienen qty_total y job_units del MB52
- [x] Garantiza que splits sin stock nuevo se resetean correctamente

**🧪 Tests implementados (100% passing):**
- [x] `test_split_job_basic` ✅ - Split básico 10 lotes → 4+6
- [x] `test_split_job_validation_errors` ✅ - Validación de parámetros
- [x] `test_split_distribution_new_stock` ✅ - Distribución a split con menor qty
- [x] `test_split_distribution_all_zero_creates_new_job` ✅ - Crear nuevo job cuando splits=0

**Documentación (especificacion.md + modelo-datos.md):**
- [x] Actualizado para detallar "Smart Sync": persistencia de lotes en splits y limpieza de jobs vacíos

**Status actual:**
- ✅ Implementación split_job() completa (120 líneas, validaciones, redistribución job_units)
- ✅ Modificación _create_jobs_from_mb52() para detectar splits y distribuir stock
- ✅ Sincronización inteligente: trackeo de jobs actualizados + reset de no actualizados
- ✅ Normalización SAP keys: tests usan pedido/posicion sin leading zeros
- ✅ Test fixture configurado: sap_material_prefixes="*" para aceptar materiales de test
- ✅ **Tests: 8/8 passing (100%)** - funcionalidad completamente validada
- ✅ **Suite completa: 20/20 tests passing** - sin regresiones

**Archivos modificados:**
- src/foundryplan/data/repository.py: 
  - split_job() método (líneas 2388-2488)
  - _create_jobs_from_mb52() modificado para splits (líneas 1595-1780)
  - Sincronización inteligente con tracking de updated_job_ids
- tests/test_job_creation.py: 4 tests de split agregados (300+ líneas)
- Fixture temp_db configurado para material prefix filter
- docs/PLAN_IMPLEMENTACION.md: Actualizado con estado completado

**Commits pendientes:** FASE 3.2 Split Management - implementación completa

---

### 3.3 Job Priority Management ✅ **COMPLETADO**

**🎨 Valores de prioridad (desde config `job_priority_map`):**
- [x] "prueba": 1 (menor = mayor prioridad)
- [x] "urgente": 2
- [x] "normal": 3 (default)

**📌 Reglas de asignación:**
- [x] **Default:** Todo job se crea con `priority` = valor "normal" (ej: 3)
- [x] **Tests automáticos:** Si `is_test=1` → `priority` = valor "prueba" (ej: 1)
- [x] **Urgentes manuales:** Usuario marca desde GUI → cambiar `priority` = valor "urgente" (ej: 2)
  - [x] Implementar método: `mark_job_urgent(job_id)` → UPDATE job SET priority = <urgente_value>
  - [x] Implementar método: `unmark_job_urgent(job_id)` → UPDATE job SET priority = <normal_value>

**🔄 Persistencia:**
- [x] `priority` es campo en tabla `job` (persistente)
- [x] Recalcular al cambiar config `job_priority_map` (lógica de actualización en `set_config`)
- [x] No recalcular automáticamente al cargar SAP (mantener marcas manuales)

**Tests:**
- [x] `test_priority.py`: 3 tests validando flujos manuales y cambio de config

---

## 4️⃣ FASE 4: DISPATCHER

### 4.1 Dispatcher Algorithm (especificacion.md línea 117)
- [x] Input: Jobs con state='queued' (V0.2 Models)
- [x] Ordenar: priority ASC, luego start_by ASC
- [x] Para cada job:
  - [x] Validar family permitida en alguna línea
  - [x] Elegir línea con menor carga actual
  - [x] Asignar a esa línea
- [x] Output: Dict structure compatible con UI legacy (`scheduler_v2`)
- [x] Integration: Repository syncs orders -> job table V0.2

**Auto-generation (especificacion.md línea 27):**
- [x] Generar automáticamente al cargar MB52 (Integrated in `pages.py`)
- [x] Regenerar al cambiar Config/recursos (Repository invalidates)

---

### 4.2 In-Progress Locks
- [x] Leer `program_in_progress_item` (jobs pinned)
- [x] Validar que siguen existiendo en `orders` (si no, limpiar)
- [x] Mantener en misma línea
- [x] Mover a TOP de línea (ordering by marked_at)
- [x] Distribuir cantidad según split_id
- Note: This logic is handled by `_apply_in_progress_locks` in Repository, bridging legacy `orders` and new `jobs`.

**Especial: Lowest-Qty Distribution (modelo-datos.md línea 376):**
- [x] Cuando nuevo stock entra con splits activos (Implemented in Phase 3.2)
- [x] Asignar al split con `min(qty_actual)` (Verified in Phase 3.2)

---

### 4.3 Resource Constraints
- [ ] Validar `job.familia` en `resource_constraint` para cada línea
- [ ] No asignar si no pasa validación
- [ ] Reportar como "no programado" si falla

---

## 5️⃣ FASE 5: PERSISTENCIA DE ESTADO

### 5.1 Save/Load Last Program
- [x] `save_last_program(process, program)` → guarda JSON en `last_program` (Existing)
- [x] `load_last_program(process)` → carga + re-aplica in-progress locks (Existing)
- [x] Lógica: splits + pins persisten, cantidad recalculada desde órdenes actuales

---

### 5.2 Manual Actions
- [x] `mark_in_progress(pedido, posicion, line_id, split_id)` → crea entry en `program_in_progress_item` (Existing)
- [x] `unmark_in_progress(pedido, posicion)` → borra locks (Existing)
- [x] `move_in_progress(pedido, posicion, new_line_id)` → cambia de línea (Existing)
- [x] Validación de config: `ui_allow_move_in_progress_line` check added.

## 6️⃣ FASE 6: UI - PÁGINAS

### 6.1 Home / Pedidos (especificacion.md línea 18-24)
- [x] Tabla: órdenes atrasadas + próximas semanas (UI pending, data accessors ready)
- [x] Gráfico histórico: toneladas atrasadas (logic fixed in `upsert_vision_kpi_daily`)
- [ ] Acciones: doble clic abre desglose; marcar como urgente
- [ ] Data source: `get_orders_overdue_rows()` + `get_orders_due_soon_rows()`
- [ ] KPI: `get_vision_kpi_daily_rows()`

**Implementación:**
- [ ] Método público: `get_orders_overdue_rows(today=None, limit=200)`
- [ ] Método público: `get_orders_due_soon_rows(today=None, days=14, limit=200)`
- [ ] Método público: `upsert_vision_kpi_daily(snapshot_date=None)`
- [ ] Método público: `get_vision_kpi_daily_rows(limit=120)`

**Desglose de pedido/posición:**
- [ ] Mostrar: `get_vision_stage_breakdown(pedido, posicion)`
- [ ] Etapas: Por programar, Por moldear, ... Bodega, Despachado
- [ ] Etapas de rechazo: Rechazo, Rech. Insp. Externa

---

### 6.2 Actualizar (especificacion.md línea 27-36)
- [ ] Upload MB52 (merge/replace modes)
- [ ] Upload Visión Planta
- [ ] Vista previa + diagnósticos:
  - [ ] Faltantes en maestro → popup `material_master`
  - [ ] Stock no usable (QC bloqueado)
  - [ ] Inconsistencias SAP (pedido/pos en MB52 sin Visión)
- [ ] Al completar: invalida órdenes + programa, regenera automáticamente

**Métodos públicos para diagnósticos:**
- [ ] `get_missing_parts_from_orders(process)`
- [ ] `get_missing_process_times_from_orders(process)`
- [ ] `get_sap_non_usable_with_orderpos_rows(limit=200)` - Stock en QC
- [ ] `get_sap_orderpos_missing_vision_rows(limit=200)` - Inconsistencias SAP

---

### 6.3 Programa (especificacion.md línea 37-46)
- [ ] Tabla: colas por línea
- [ ] Resalta: tests (icon), urgentes (icon)
- [ ] Acciones: 
  - [ ] Clic marca "en proceso" (pin a línea, flota a TOP)
  - [ ] Doble clic: desglose (similar a Pedidos)
  - [ ] Split: divide pedido/posición en 2 jobs balanceados
  - [ ] Mover a otra línea (si config lo permite)
- [ ] Regenera al cargar MB52 o cambios Config

---

### 6.4 Plan (especificacion.md línea 47-54)
- [ ] Semanal: qué moldear para cumplir entregas
- [ ] Avance de moldeo: unidades moldeadas vs total
- [ ] Simular: cambiar fecha deseada, ver impacto
- [ ] Guardar decisiones

**Notas:**
- [ ] Especial: Moldeo es proceso especial que usa plan
- [ ] Cálculo: $\text{moldeadas} = \text{cantidad} - (\text{por\_fundir} - \text{stock\_moldes\_no\_fundidos}) \times \text{piezas\_por\_molde}$

---

### 6.5 Config (especificacion.md línea 55-66)
- [ ] Parámetros: nombre planta, centro, almacenes, prefijos, flags
- [ ] Orden de prioridades: map {prueba, urgente, normal} → números
- [ ] Procesos + Líneas: CRUD con restricciones de familia
- [ ] Familias: CRUD

**Al cambiar:**
- [ ] Invalida Programa + Plan
- [ ] Regenera colas automáticamente

---

### 6.6 Maestro de Materiales (especificacion.md línea 67-71)
- [ ] CRUD: familia, tiempos (vulcanizado/mecanizado/inspección), atributos
- [ ] Búsqueda y filtrado
- [ ] Doble clic edita; bulk delete

**Al cambiar:**
- [ ] Cambios en tiempos → invalida Plan
- [ ] Cambios en familia → invalida Programa

---

## 7️⃣ FASE 7: VALIDACIONES & ERRORES

### 7.1 Validaciones al Import
- [ ] Columnas requeridas presentes
- [ ] SAP keys válidos (normalizables)
- [ ] No hay múltiples materiales por pedido/pos
- [ ] Material existe en maestro (o popup para crear)

---

### 7.2 Validaciones al Dispatch
- [ ] Job elegible en alguna línea (family check)
- [ ] Línea no violaría restricciones
- [ ] Stock disponible positivo

---

### 7.3 Reportes de Diagnóstico
- [ ] SAP rebuild diagnostics (counters de usable/missing)
- [ ] Stock bloqueado (en QC)
- [ ] Inconsistencias SAP (ped/pos en MB52 sin Visión)
- [ ] Partes sin maestro
- [ ] Partes sin tiempos de proceso

---

## 8️⃣ FASE 8: TESTING

### 8.1 Unit Tests
- [ ] `test_scheduler.py` - Dispatcher algorithm
- [ ] Job priority calculation
- [ ] Split creation & distribution
- [ ] In-progress locks persistence
- [ ] SAP import edge cases

### 8.2 Integration Tests
- [ ] Import MB52 + Visión → rebuild orders
- [ ] Generate dispatch queue
- [ ] Apply locks → regenerate queue
- [ ] Save/load last program
- [ ] Config changes invalidate

### 8.3 UI Tests
- [ ] Load Home page
- [ ] Upload files
- [ ] Mark in-process
- [ ] Create split
- [ ] Change config

---

## 📅 TIMELINE

| Fase | Tareas | Duración | Prioridad |
|---|---|---|---|
| 1 | Tablas & DB | 2-3 dias | 🔴 CRÍTICA |
| 2 | SAP Import | 3-4 dias | 🔴 CRÍTICA |
| 3 | Job Calc | 2-3 dias | 🔴 CRÍTICA |
| 4 | Dispatcher | 3-4 dias | 🔴 CRÍTICA |
| 5 | Persistencia | 1-2 dias | 🔴 CRÍTICA |
| 6 | UI - Páginas | 5-7 dias | 🟠 ALTA |
| 7 | Validaciones | 2-3 dias | 🟠 ALTA |
| 8 | Testing | 3-4 dias | 🟡 MEDIA |

**Total estimado:** 4-5 semanas para MVP funcional

---

## ✅ CHECKLIST FINAL

Antes de considerar v0.2 "done":

- [ ] Todas las tablas en `modelo-datos.md` existen en DB
- [ ] Todos los métodos públicos en `especificacion.md` implementados
- [ ] Todas las páginas funcionales (Home, Actualizar, Programa, Plan, Config, Maestro)
- [ ] Auto-generación de colas al cargar MB52
- [ ] Pinning & locks persisten a través de recálculos
- [ ] Tests alfanuméricos detectados automáticamente
- [ ] KPI diario funcional para gráfico de atrasos
- [ ] Documentación matches código (no hay discrepancias)

---

**Fin de Plan**

Marcar items como ✅ cuando se completen.  
Actualizar estado regularmente para seguimiento.

