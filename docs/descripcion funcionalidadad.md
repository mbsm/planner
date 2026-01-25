
# FoundryPlanner – Descripción funcional (SAP)

## Objetivo
El planificador genera el **programa (secuencia/colas de trabajo)** por cada línea productiva de terminaciones.

La diferencia clave con la versión anterior es que el programa se construye **solo con piezas que realmente existen** en stock (SAP), no desde un Excel de “pedidos”.

## Fuentes de datos SAP
La app consume dos archivos descargados desde SAP:

### 1) MB52 (stock por pieza)
Contiene una línea por cada pieza/lote disponible.

Columnas relevantes:
- **Material**: número de parte.
- **Centro**: planta. Default esperado: **4000**.
- **Almacén**: almacén donde está el material. En terminaciones: **4035**.
- **Lote**: correlativo de la pieza (se espera numérico).
- **Libre utilización**: `1` usable, `0` no usable.
- **Documento comercial**: pedido de venta asociado a la pieza.
- **Posición (SD)**: posición del pedido.
- **En control calidad**: `1` en control de calidad (no usable), `0` usable.

### 2) Visión Planta (pedido/posición)
Contiene una línea por cada **pedido + posición**.

Columnas relevantes:
- **Pedido**: coincide con “Documento comercial” de MB52.
- **Pos.**: coincide con “Posición (SD)” de MB52.
- **Cod. Material**: número de parte (referencial).
- **Fecha de pedido**: fecha base usada para priorización/planificación.

## Filtro de piezas usables (MB52)
Una pieza se considera usable si cumple:
- `Centro = 4000`
- `Almacén = 4035`
- `Libre utilización = 1`
- `En control calidad = 0`

## Cruce MB52 ↔ Visión Planta
La app cruza la información para obtener la fecha de cada pedido/posición:
- MB52.`Documento comercial` + MB52.`Posición (SD)`
- debe existir en Visión Planta como Visión.`Pedido` + Visión.`Pos.`

La **fecha utilizada** para planificar es **Visión Planta → Fecha de pedido**.

## Construcción de órdenes internas (sin rangos de correlativos)
Desde las piezas usables se genera una orden por **pedido/posición/material**:

1. Se agrupan piezas por **Pedido + Posición + Material**.
2. La orden se transforma en una fila de planificación con:
  - `pedido`, `posicion`, `numero_parte`
  - `cantidad = número de piezas (lotes) existentes en stock`
  - `fecha` = Visión Planta “Fecha de pedido”

Nota: los correlativos (lotes) no se muestran en la UI en esta etapa.

## Prioridad y secuenciación
Para priorizar, se calcula la **última fecha de inicio**:

`start_by = fecha_pedido - (vulcanizado_dias + mecanizado_dias + inspeccion_externa_dias)`

Luego:
1. Se ordenan rangos por `start_by` ascendente (más urgente primero).
2. Se van asignando a líneas secuencialmente, respetando:
    - Restricción de familias permitidas por línea.
    - Selección de la línea elegible con menor carga (heurística actual).

### Pruebas (lotes alfanuméricos)
En Terminaciones, si el lote contiene letras se considera **prueba** y se prioriza automáticamente.

El correlativo numérico se obtiene desde el **prefijo numérico** del lote (primer grupo de dígitos). Ejemplo:
- `0030PD0674` → correlativo `30`

## Maestro local (se mantiene en la app)
Estos datos **NO vienen de SAP** y se mantienen localmente en la app (SQLite):
- **Familia por número de parte**.
- **Tiempos de proceso** por número de parte (días):
  - `vulcanizado_dias`, `mecanizado_dias`, `inspeccion_externa_dias`

## Manejo de estado al subir archivos (importante)
Al subir un archivo SAP:
- La app **borra primero** los datos previamente importados de ese mismo archivo (tabla MB52 o Visión Planta) y luego carga el nuevo contenido.
- El programa y los rangos derivados se **recalculan** cuando están disponibles ambos archivos (MB52 + Visión) y el centro/almacén están configurados.
- El **maestro de producto** (familias y tiempos) **se conserva**: no se borra al importar SAP.

## Operación rápida (checklist)
1. Ir a **Actualizar**.
2. Verificar parámetros SAP (por defecto: Centro **4000**, Almacén terminaciones **4035**) y presionar **Guardar** si se modifican.
3. Subir **MB52**.
4. Subir **Visión Planta**.
5. Revisar la “Vista previa”:
  - Debe haber **piezas usables** y **cruce con Visión**.
  - Debe haber **Órdenes** (> 0) para poder programar.
6. Si aparecen pendientes: completar **Config > Familias** y **Config > Tiempos de proceso**.
7. Ir a **Programa** y verificar colas por línea.

## Módulos adicionales

### Home: KPI diario (Visión)
Al subir Visión Planta, la app calcula y guarda un snapshot diario:
- `tons_por_entregar`: tons pendientes (según solicitado/bodega/despachado)
- `tons_atrasadas`: subset donde `fecha_entrega` < fecha del snapshot

Se muestra un gráfico histórico y tabla de detalle en Home.

### Avance Producción (MB52)
Al subir MB52 en modo **replace**, la app compara contra el MB52 anterior (en Terminaciones) y genera un reporte de:
- **salidas brutas**: piezas/lotes que estaban y ya no están
- mapeo de salidas al **último programa** por rango de correlativos
- sección adicional de **salidas no programadas** (no mapeadas)


