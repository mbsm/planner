# FoundryPlanner (Planificación Integral de Planta)

Plataforma de planificación de producción (NiceGUI + SQLite) con **dos capas**:

1. **Planificación Estratégica (Semanal):** Genera planes de producción respetando capacidades globales de la planta (moldeadoras, pailas, flasks). Usa optimización matemática (MIP) para minimizar atrasos ponderados. [*Powered by [foundry_planner_engine](https://github.com/mbsm/foundry_planner_engine)*]
2. **Despacho Táctico (Horario):** Colas de trabajo por línea derivadas del plan semanal + stock usable (SAP MB52) + Visión Planta, con maestro local (familias + tiempos).

Incluye:
- **Plan Semanal**: asignación de molds por orden/semana respetando restricciones de planta
- **Programas de Producción** por línea (derivados del plan semanal)
- **KPI diario** (Visión): tons por entregar y tons atrasadas + gráfico histórico
- **Avance Producción** (MB52): reporte de salidas brutas vs MB52 anterior, mapeadas al último programa

## Requisitos
- **Windows** (primary) o **macOS/Linux** (development)
- Python 3.14+
- Submódulos inicializados: `git submodule update --init --recursive`

## Instalación
Desde la carpeta raíz del workspace (donde está `.venv`):

1. Inicializa el motor estratégico vendorizado:

```bash
git submodule update --init --recursive
```

2. Instala dependencias Python:

**Windows:**
```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS/Linux:**
```bash
.venv/bin/python -m pip install -r requirements.txt
```

## Ejecutar

**Windows:**
```powershell
.venv\Scripts\python.exe run_app.py --port 8080
```

**macOS/Linux:**
```bash
.venv/bin/python run_app.py --port 8080
```

Luego abre http://localhost:8080

Navegación en la app:
- **Dashboard**: métricas rápidas (KPI Visión, avance producción).
- **Plan Semanal** *(NEW)*: vista estratégica del plan semanal; detalles de latencias por orden.
- **Programa**: colas por línea derivadas del plan semanal (se guarda en SQLite como "última programa").
- **Actualizar**: carga de MB52 + Visión Planta desde Excel (dispara replanificación automática).
- **Avance Producción**: reporte de salidas (MB52) por línea vs el último programa.
- **Config** (dropdown): Configuración de líneas / Familias / Tiempos de proceso / Parámetros de capacidad.

## Operación rápida
1. Ir a **Actualizar** y verificar Centro/Almacén (defaults: `4000` / `4035`).
2. Subir **MB52** y luego **Visión Planta**.
3. Revisar “Vista previa”: debe haber **Rangos > 0**.
4. Si hay pendientes, completar **Familias** y **Tiempos de proceso**.
5. Ir a **Programa**.

Notas:
- La app lee **solo la primera hoja** del Excel.
- La importación es **SAP-driven**: la tabla `orders` se reconstruye uniendo MB52 + Visión.
- Los lotes alfanuméricos en Terminaciones se tratan como **pruebas** (prioridad) y su correlativo se toma desde el **prefijo numérico**.

## Tests

**Windows:**
```powershell
.venv\Scripts\python.exe -m pytest
```

**macOS/Linux:**
```bash
.venv/bin/python -m pytest
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
- **El plan semanal se recalcula automáticamente** (aprovecha cambios en SAP + config).
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

## Arquitectura de Dos Capas

See [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md) for detailed two-layer design:
- **Layer 1 (Strategic):** Weekly MIP-based planner using [foundry_planner_engine](https://github.com/mbsm/foundry_planner_engine)
- **Layer 2 (Tactical):** Heurístico basado en MB52 (prioridad asc, luego fecha_entrega - tiempos). No consume el plan semanal hoy; solo el futuro dispatcher de moldeo usará `plan_molding` para secuenciar por patrón.

## Notas
- Persistencia en SQLite local (archivo `.db`).
- **Planificación estratégica (semanal):** Optimización matemática (minimiza atrasos ponderados).
- **Despacho táctico (horario):** Heurística (orden por fecha entrega + carga de línea).
- Capacidades respetadas: flasks, tonelaje de paila (melt deck), horas/línea, límites por patrón.

## Documentación
- Arquitectura de integración: [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md)
- Formatos SAP (MB52/Visión): [docs/formato_excel.md](docs/formato_excel.md)
- Descripción funcional (SAP): [docs/descripcion funcionalidadad.md](docs/descripcion%20funcionalidadad.md)
- Parámetros/configuración: [docs/configuracion.md](docs/configuracion.md)
- Solución de problemas: [docs/troubleshooting.md](docs/troubleshooting.md)

