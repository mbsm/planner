# Foundry Plan

Sistema de planificación y despacho (planner + dispatcher) para plantas make-to-order, a partir de **stock SAP (MB52)** + **Visión Planta** + maestro local, persistiendo en SQLite.

App web (NiceGUI) que genera colas de trabajo por línea para múltiples procesos (Terminaciones, Mecanizado, etc.).

Incluye:
- **Programas de Producción** por línea (por proceso/almacén)
- **KPI diario** (Visión): tons por entregar y tons atrasadas + gráfico histórico
- **Avance Producción** (MB52): reporte de salidas brutas vs MB52 anterior, mapeadas al último programa

## Requisitos
- Windows
- Python 3.11+

## Instalación
Desde la carpeta raíz del workspace (donde está `.venv`):

```powershell
# instalar dependencias
\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Ejecutar

```powershell
\.venv\Scripts\python.exe run_app.py --port 8080
```

Luego abre http://localhost:8080

Navegación en la app:
- **Pedidos**: resumen de pedidos atrasados/próximos + KPI.
- **Programa**: colas por línea (se guarda en SQLite como “último dispatch”).
- **Actualizar**: carga de MB52 + Visión Planta desde Excel.
- **Plan**: simulación de moldeo y análisis de impacto.
- **Config** (dropdown): Parámetros / Procesos y Líneas / Familias / Maestro de Materiales.

## Operación rápida
1. Ir a **Actualizar** y verificar Centro/Almacén (defaults: `4000` / `4035`).
2. Subir **MB52** y luego **Visión Planta**.
3. Revisar “Vista previa”: debe haber **Rangos > 0**.
4. Si hay pendientes, completar **Familias** y **Tiempos de proceso**.
5. Ir a **Programa**.

Notas:
- La app lee **solo la primera hoja** del Excel.
- La importación es **SAP-driven**: la tabla `orders` se reconstruye uniendo MB52 + Visión.
- Los lotes alfanuméricos (contienen letras) se tratan como **pruebas** (prioridad) y su correlativo se toma desde el **prefijo numérico**.
- La fecha base usada hoy para reconstruir `orders` y planificar es **Visión → Fecha de pedido** (`fecha_de_pedido`).

## Tests

```powershell
\.venv\Scripts\python.exe -m pytest
```

## Datos esperados (SAP)
En **Actualizar** se suben 2 archivos `.xlsx` (primer sheet):

- **MB52**: stock por pieza/lote.
- **Visión Planta**: pedido/posición con fechas.

La app filtra piezas usables desde MB52 con:
- `Centro = 4000`
- `Almacén = 4035`
- `Libre utilización = 1`
- `En control calidad = 0`

Luego cruza MB52 (Documento comercial + Posición SD) contra Visión (Pedido + Pos.) para recuperar la **Fecha de pedido**.

Notas de estado (importante):
- Al subir MB52 o Visión, la app **reemplaza** el contenido previamente importado de ese mismo archivo.
- El programa/rangos se recalculan cuando están ambos archivos disponibles.
- El maestro local de producto (familias + tiempos) **se mantiene** y no se borra al importar SAP.

Las **familias** (`numero_parte` → `familia`) se administran dentro de la app en **Config > Familias** y se guardan en SQLite.

Además existe un **catálogo de familias** (CRUD) para que las opciones existan aunque no estén asignadas aún a ninguna parte.

## Maestro de piezas (post-proceso)
Además de la familia, la app mantiene tiempos (en **días**) para procesos posteriores a terminación:
- `vulcanizado_dias`
- `mecanizado_dias`
- `inspeccion_externa_dias`

El criterio de prioridad del programa usa:
`fecha_entrega - (vulcanizado_dias + mecanizado_dias + inspeccion_externa_dias)`

Si importas pedidos con números de parte que no tienen estos tiempos definidos, la app los pedirá en **Config > Tiempos de proceso**.

Al importar pedidos o cambiar configuración (familias/tiempos/líneas), la app intenta **actualizar automáticamente el programa**. Si faltan datos, muestra un aviso indicando qué completar.

## Notas
- Persistencia en SQLite local (archivo `db/foundryplan.db`).
- El scheduler v1 es heurístico (orden por fecha y asignación a líneas elegibles por familia).

## Documentación

**Para usuarios:**
- [Guía de operación](docs/operacion.md) — cómo usar la aplicación, configuración, troubleshooting

**Para desarrolladores:**
- [Especificación (diseño)](docs/especificacion.md) — qué queremos construir (planner + dispatcher)
- [Estado / checklist](docs/estado.md) — avance de implementación
- [As-built (implementado)](docs/implementado.md) — qué está hecho hoy
- [Modelo de datos](docs/modelo-datos.md) — schema SQLite + mapeo SAP (WIP)
- [Guía de desarrollo](docs/desarrollo.md) — arquitectura interna, tests, debugging (WIP)

