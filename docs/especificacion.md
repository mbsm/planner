# Foundry Plan — Especificación v0.2

Este documento define el sistema Foundry Plan en la versión 0.2: una solución de planificación y despacho para plantas make-to-order.
Nota: aquí hablamos en términos de modelo interno; el mapeo detallado desde SAP/Excel se documenta aparte.

## 1) Descripción (visión)
Foundry Plan automatiza la planificación y secuenciación de producción de una planta make-to-order usando:
- **SAP MB52** (stock / unidades físicas por almacén)
- **Visión Planta** (pedido/posición + atributos, fechas y avance)
- **Maestro local** de materiales (atributos que SAP no tiene o no es confiable para planificación)

El objetivo es que la solución sea **configurable** para múltiples plantas con el mismo modelo de información (cambian códigos de almacén/centro, líneas y restricciones).

## 2) Arquitectura (alto nivel)
- UI web para cargar datos, configurar planta/procesos, y visualizar plan/colas.
- Backend Python con:
  - ingestión (snapshot) desde Excel; interfaz directa SAP a futuro.
  - generación de entidades internas (Jobs / colas).
  - persistencia en SQLite.
- Autenticación: usuarios deben autenticarse con **Microsoft Entra ID** (Azure AD) antes de acceder a la aplicación.

### 2.1 Páginas (v0.2)

**Pedidos (Home)**
- Resumen de pedidos: atrasados (fecha entrega < hoy) y próximas semanas (próximos 6 semanas).
- Gráfico histórico de KPI (toneladas atrasadas desde Visión Planta).
- Acciones: doble clic en fila abre desglose de la orden (pedido/posición) con progreso de stock y avance; opción de marcar como urgente.
- Actualización: cada vez que se carga Visión Planta; datos persistentes: marcas de urgente (afectan prioridad).

**Actualizar**
- Carga MB52 (stock por lote) y Visión Planta (pedidos + atributos).
- Vista previa: filas cargadas, diagnósticos (faltantes en maestro, stock no usable, cruces SAP).
- Si faltan materiales, popup para completar familia y tiempos.
- Acciones: subir MB52 (merge/replace), subir Visión Planta.
- Comportamiento: al cargar Visión, invalida "Pedidos" (requiere recálculo); al cargar MB52, **genera automáticamente** nuevas colas de dispatch por proceso.
- Fuente de verdad: SAP. Las cargas **reemplazan** completamente los snapshots y deben reflejar movimientos reales (desaparición de pedidos en Visión, traslado entre almacenes por proceso).
- Si existe `pedido/posicion` en MB52 que no existe en Visión, se registra en **errores de dispatch** para el proceso.

**Programa** (por proceso: Terminaciones, Mecanizado, etc.)
- Colas por línea/recurso con pedido/posición/parte/lotes/fecha entrega.
- Resalta pruebas (icon science) y urgentes (icon priority_high).
- Muestra órdenes no programadas si hay errores (familia no habilitada).
- Acciones: clic en fila marca "en proceso" (pin) → se fija a su línea y queda en las primeras posiciones; doble clic abre desglose; opción de **split** para dividir un pedido/posición en múltiples jobs.
- Se permite **mover manualmente** jobs entre líneas si la configuración lo habilita.
- Actualización: se regenera automáticamente cuando se carga MB52 o hay cambios en Config; datos persistentes: pines "en proceso".

**Plan** (simulación de moldeo)
- Muestra plan semanal propuesto: qué y cuándo moldear para cumplir entregas.
- Incluye **avance de moldeo** por pedido/posición (unidades moldeadas vs total) y % de avance.
- Permite "simular": seleccionar un pedido/posición e indicar fecha deseada de entrega; sistema muestra impacto (qué otros pedidos se ven afectados, si es posible, carga resultante).
- Acciones: visualizar plan base; simular cambios de fecha; guardar decisiones de plan.
- Actualización: se regenera al cambiar Config o por request del usuario. Persistente: decisiones manuales sobre fechas.

**Config > Parámetros**
- Nombre planta, centro SAP, almacenes por proceso, prefijos material, flags UI.
- Orden de prioridades por tipo (configurable): mapa de prioridad numérica para `prueba`, `urgente`, `normal` (menor número = mayor prioridad).
- Acciones: editar y guardar (invalida Programa y Plan).
- Comportamiento: cambios fuerzan regeneración automática de colas de dispatch.

**Config > Procesos y Líneas**
- Define procesos (Terminaciones, Mecanizado, etc.) y almacén asociado.
- Para cada proceso: líneas/recursos con nombre y restricciones (familias permitidas).
- Acciones: agregar/editar/eliminar procesos; agregar/editar/eliminar líneas; editar familias por línea.
- Comportamiento: cambios invalidan Programa (requiere recálculo heurístico).

**Familias**
- Catálogo de familias (CRUD) con conteo de partes asignadas.
- Acciones: agregar; doble clic edita/renombra; eliminar (opción: reasignar a "Otros").
- Comportamiento: cambios invalidan Programa si afectan asignaciones.

**Maestro de Materiales**
- Edita por material: familia, tiempos (vulcanizado/mecanizado/inspección días), atributos (perf. inclinada, sobre medida).
- Búsqueda y filtrado.
- Acciones: doble clic abre editor; bulk delete.
- Comportamiento: cambios en tiempos invalidan Plan (afecta cálculo de start_by). `peso_unitario_ton` se actualiza desde Visión; si cambia, se solicita actualizar `peso_bruto_ton`.

### 2.2 Principios de operación (v0.2)
Separación de dos ritmos:
- **Planner**: corre semanal (o ad-hoc) para planificación de moldeo. Produce plan con simulación de capacidad.
- **Dispatcher**: se ejecuta automáticamente al cargar MB52 (y también ante cambios de Config) para secuenciar el stock disponible, usando una **prioridad numérica única** por job (configurable por tipo: prueba/urgente/normal).

## 3) Conceptos y modelo de datos (conceptual)

### 3.1 Identidades SAP (fuente)
- **OrderPos**: `(pedido, posicion)`.
  - Invariante: `pedido-posicion` corresponde a **un solo material**.
- **StockUnit** (MB52): cada fila representa **1 unidad física** asociada a `(pedido, posicion, material, correlativo/lote)`.

### 3.2 Entidad operativa interna: Job
Un **Job** es la unidad del dispatch:
- Identidad: `(planta, proceso, pedido, posicion, material, job_id)`.
- Por defecto: **1 job == 1 pedido/posición** por proceso.
- El usuario puede **splittear** un pedido/posición en múltiples jobs asociados al mismo `pedido/posicion` (para gestionar partes independientes).
    - **Persistencia de Splits**: Al actualizar MB52, el sistema respeta la asignación de lotes existentes a sus jobs (splits).
    - **Nuevos lotes**: Si aparecen nuevos lotes en SAP, se asignan automáticamente al job (split) con menor carga actual.
    - **Limpieza**: Si un job se queda sin lotes (stock 0 en SAP), el sistema lo elimina automáticamente.
- Excepción automática: si hay **lotes alfanuméricos** en un almacén, se crea/actualiza un job de **pruebas** que agrupa esos correlativos.
- Contiene un **conjunto de correlativos** (NO se asume contiguo).
  - El sistema debe poder representar: listas explícitas, múltiples rangos, y/o “splits” parciales.
- Cada correlativo/lote puede estar marcado como **prueba**; el modelo debe soportar esta marca a nivel de lote/ítem.
- El **qty** del Job se calcula como el **conteo de lotes** asignados (derivado de `job_unit`); no existe `qty_total` ni `qty_remaining`.
- Un Job puede estar en estado: `queued | in_progress | done | blocked | cancelled`.

### 3.2.1 Dispatch por proceso (diseño)
Una planta tiene **N procesos configurables** y cada proceso tiene su propio dispatcher.
- Cada **proceso** tiene un **almacén asociado** (stock real que alimenta el dispatch).
- Cada **proceso** tiene **una o más líneas/recursos** de ejecución.
- Cada **línea** tiene un **nombre** y un conjunto de **restricciones de procesamiento**.

Las restricciones:
- Se expresan como predicados sobre **atributos del producto** (ej: familia, perforación inclinada, sobre medida, u otros).
- Son **configurables por proceso**: distintos procesos pueden usar atributos distintos.
- Se aplican para determinar elegibilidad: `job` es elegible en una `línea` si cumple todas las restricciones configuradas para esa línea (para ese proceso).

### 3.3 Planner vs Dispatcher (contrato)

**Planner (semanal)**
- Alcance: planifica **solo el inicio del proceso** (Moldeo es el único proceso especial).
- Output: listado semanal de **cuántas unidades (moldes) de cada pedido/posición** se deben moldear.
- Incluye el **avance de moldeo** por pedido/posición (% y unidades).
- No asigna secuencia fina por correlativo; es un plan agregado por semana.

**Dispatcher (alta frecuencia)**
- En general, el dispatcher **NO usa el plan**.
- Su input es la **ejecución real**: stock disponible en el almacén del proceso + estados reales.
- Output: secuencia/cola por línea/recurso para procesar el stock.

**Excepción**
- El **dispatcher de Moldeo** usará el plan, o más específicamente la diferencia **Plan vs Real** (delta) para decidir qué despachar.

## 4) Reglas de negocio (diseño)
- Restricciones por recurso/línea: configurables por proceso y basadas en atributos del producto.
- “Pruebas”: un Job puede marcarse como prueba (regla exacta a definir en mapeo SAP, pero el modelo soporta `is_test`).
- Prioridades: manuales y por tipo (test/urgente/normal) deben persistir como decisiones operativas.
- El estado operativo (“en proceso”, splits, bloqueos) debe sobrevivir a refreshes de SAP.
- Los splits se actualizan **solo** con MB52 (stock real). Si un pedido/posición desaparece de Visión, **no** se modifican los splits.
- Si hay splits activos y llega nuevo stock, se asigna al **split con menor cantidad**.
- Si ambos splits se vacían y luego reaparece stock, se crea **un solo job** nuevo (sin reutilizar splits anteriores).
- **Movimientos SAP**: cuando un pedido se despacha, desaparece de Visión Planta. Cuando un pedido/posición termina un proceso, desaparece del almacén de ese proceso y aparece en el almacén del siguiente.
- La aplicación **no mueve stock**: solo refleja SAP. Por eso, cada carga reemplaza snapshots y **recalcula** jobs/colas según el stock real por almacén.
- Si un pedido/posición desaparece de Visión, el job se considera **cerrado** (se mantiene histórico, no se regenera).
- Si un pedido/posición ya no tiene unidades en un almacén de proceso, su job en ese proceso se **cierra** o queda sin cola; si reaparecen unidades en futuras cargas, se reabre o se crea un job nuevo.
- **Avance de moldeo (especial)**: se calcula a partir de Visión + MB52 y `piezas_por_molde`.
  - Visión reporta **unidades (piezas)**; MB52 reporta **moldes**.
  - Cálculo base: $\text{moldeadas} = \text{cantidad\_pedido} - (\text{por\_fundir} - \text{stock\_moldes\_no\_fundidos}) \times \text{piezas\_por\_molde}$.
  - Debe tolerar variaciones por rechazos (cantidad pedido puede variar). El algoritmo exacto se define en el mapeo SAP.

Reglas de prioridad del dispatcher:
- Se asigna un **número de prioridad único** por job (menor = mayor prioridad).
- La prioridad se calcula por tipo según configuración (por defecto: `prueba=1`, `urgente=2`, `normal=3`).
- El ordenamiento final del dispatcher es: `priority` ascendente, luego `start_by` ascendente.
- La prioridad debe existir en el modelo como dato persistente y ser recalculable al cambiar la configuración.
- Los jobs **heredan la prioridad** del `pedido/posicion` (urgente/normal) **salvo** el job de pruebas, que usa prioridad `prueba`.

## 5) Tablas internas (v0.2)

Decisión de arquitectura: **1 instancia/DB por planta** (simplifica diseño; multi-planta se resuelve con múltiples instancias).

### 5.1 Configuración y maestro
- `app_config`: parámetros de planta (nombre, centro, almacenes por proceso, prefijos, flags UI).
- `family_catalog`: catálogo de familias.
- `material_master`: maestro local por material (familia, tiempos, peso, flags, **piezas_por_molde**).
- `process`: catálogo de procesos (clave/label) + `almacen` asociado.
- `process_attribute_def`: define qué atributos usa cada proceso como restricciones (schema de restricciones por proceso).
  - **Set mínimo de tipos para partir**:
    - `bool`: permitido si `true` o `false` (ej: `sobre_medida=false`).
    - `enum`: permitido si valor está en un set (ej: `familia IN ('Parrillas','Lifters')`).
    - `number_range`: permitido dentro de rangos (futuro, si aplica).
- `resource` (líneas): recursos por proceso (line_id/nombre/capacidad).
- `resource_constraint`: reglas por línea y por atributo (ej: familia ∈ {Parrillas,Lifters}; sobre_medida=false; etc.).

### 5.2 Staging SAP y Jobs
- `sap_mb52_snapshot`: filas de MB52 (unidad física por lote/correlativo) + timestamp.
- `sap_vision_snapshot`: filas de Visión (pedido/posición + atributos) + timestamp.
- `job`: entidad core con `(process, pedido, posicion, material, job_id)` + `is_test`, `priority` (numérico, menor = mayor prioridad).
- `job.qty`: cantidad de unidades/lotes asignados al Job (derivado desde `job_unit`).
- `job_unit`: mapeo `job_id` ↔ lotes concretos (lista explícita) para soportar Jobs no-contiguos y splits.

### 5.3 Dispatcher y estado operativo
- `dispatch_queue_run`: una "corrida" del dispatcher con timestamp, versión algo, snapshots source.
- `dispatch_queue_item`: cola por línea/recurso con `(run_id, resource_id, seq, job_id, qty, pinned, notes)`.
- `dispatch_error`: errores de dispatch por corrida (ej: `MB52_SIN_VISION`).
- `last_dispatch`: último dispatch persistido por proceso para reconocer lo que falta vs lo ejecutado.
- `dispatch_in_progress` / `dispatch_in_progress_item`: pines por línea (split-aware) que sobreviven refreshes SAP.
- `job_unit` (requerido en este diseño):
  - mapea `job_id` ↔ correlativos concretos (lista explícita) para soportar Jobs no-contiguos.
  - permite representar "splits" y consumos parciales por correlativo.
- `dispatch_queue_run`:
  - una "corrida" del dispatcher: `run_id`, `process`, `generated_at`, `source_snapshot_at`, `algo_version`.
  - permite representar “splits” y consumos parciales por correlativo.
- `dispatch_queue_run`:
  - una “corrida” del dispatcher: `run_id`, `plant_id`, `process`, `generated_at`, `source_snapshot_at`, `algo_version`.
- `dispatch_queue_item`:
  - cola por recurso: `run_id`, `resource_id`, `seq`, `job_id`, `qty`, `pinned`, `notes`.
- `last_dispatch`:
  - último dispatch persistido por proceso (equivalente conceptual de `last_program`).
- `dispatch_in_progress` / `dispatch_in_progress_item`:
  - estado operativo/pines por línea (split-aware), equivalente conceptual de `program_in_progress(_item)`.

### 5.4 Plan semanal (solo moldeo)
- `weekly_plan_run`: `plan_id`, `week_start`, `generated_at`, `source_snapshot_at`, `notes`.
- `weekly_plan_item`: `plan_id`, `(pedido,posicion,material)`, `qty_planned`.

## 6) Fuentes de verdad
- Implementación actual (as-built): ver `docs/implementado.md`.
- Checklist de avance: ver `docs/estado.md`.
