# Foundry Plan — Guía de operación

Guía para usuarios/operadores de la aplicación.

## 1) Inicio rápido
### 1.1 Ejecutar la aplicación
```powershell
.venv\Scripts\python.exe run_app.py --port 8080
```
Abrir http://localhost:8080

### 1.2 Workflow típico
1. **Actualizar**: subir MB52 + Visión Planta
2. **Config**: verificar centro/almacenes y maestro de materiales
3. **Programa/Dispatch**: visualizar colas por línea

### 1.3 Autenticación
- Ingreso con cuenta Microsoft de la organización (OAuth).
- Si no estás autenticado, la aplicación redirige al inicio de sesión.

### 1.4 Pantallas
- **Pedidos (Home)**: resumen de pedidos atrasados y próximas semanas + histórico KPI (Visión).
  - Actualización: al cargar Visión Planta. Persistente: marcas de urgente.
  - Acciones: doble clic abre desglose de orden; opción de marcar como urgente.
- **Actualizar**: carga MB52 + Visión y diagnósticos de faltantes.
  - Comportamiento: al cargar Visión, invalida Pedidos; al cargar MB52, invalida Programa.
  - Acciones: subir MB52 (merge/replace), subir Visión Planta.
- **Programa**: colas por línea por proceso; errores por familia no habilitada; pines "en proceso".
  - Actualización: automática cuando hay cambios en Config o cargas SAP. Persistente: pines "en proceso".
  - Acciones: clic marca "en proceso" (pin), doble clic abre desglose, regenerar programa.
- **Plan**: simulación de moldeo; muestra plan semanal y el **avance de moldeo** por pedido/posición (%).
  - Actualización: se regenera al cambiar Config o por request del usuario. Persistente: decisiones manuales sobre fechas.
  - Acciones: visualizar plan, simular cambios de fecha (impacto en otros pedidos), guardar decisiones.
- **Config > Parámetros**: centro SAP, almacenes por proceso, prefijos material, flags UI.
  - Comportamiento: cambios invalidan Programa y Plan (regeneración automática).
  - Acciones: editar y guardar.
- **Config > Procesos y Líneas**: define procesos (Terminaciones, Mecanizado, etc.) y almacén; líneas y familias permitidas por línea.
  - Comportamiento: cambios invalidan Programa.
  - Acciones: agregar/editar/eliminar procesos; agregar/editar/eliminar líneas.
- **Familias**: catálogo de familias con conteo de partes asignadas.
  - Comportamiento: cambios invalidan Programa si afectan asignaciones.
  - Acciones: agregar; doble clic edita/renombra; eliminar (opción: reasignar a "Otros").
- **Maestro materiales**: edita por material: familia, tiempos (vulcanizado/mecanizado/inspección días), atributos (perf. inclinada, sobre medida) y **piezas_por_molde**.
  - Comportamiento: cambios en tiempos invalidan Plan.
  - Acciones: búsqueda/filtrado; doble clic abre editor; bulk delete.

## 2) Flujo de trabajo
### 2.1 Carga inicial
1. Verificar parámetros: Centro SAP, almacenes por proceso, prefijos material.
2. Configurar procesos y líneas: definir qué líneas existen y qué familias cada línea puede procesar.
3. Cargar maestro de materiales: completar familias y tiempos para los materiales que usas.

### 2.2 Uso diario
1. **Actualizar**: subir MB52 + Visión Planta. El sistema recalcula programas automáticamente.
2. **Pedidos**: revisar pendientes atrasados y próximas semanas.
3. **Programa**: ver colas por línea; marcar órdenes "en proceso" cuando la línea comienza.
4. **Plan**: revisar avance de moldeo y comparar lo programado vs lo disponible.

## 3) Carga de datos
### 3.1 MB52
- Formato esperado (ver modelo-datos.md)
- Validaciones y diagnósticos

### 3.2 Visión Planta
- Formato esperado
- Snapshot diario para KPI

## 4) Programas y dispatch
### 4.1 Visualizar programa por proceso
### 4.2 Marcar items "en proceso" (pinning)
### 4.3 Splits y ajustes manuales

## 5) Reportes
### 5.1 Dashboard / KPI
### 5.2 Avance de producción

## 6) Solución de problemas
### 6.1 No aparecen pedidos / programa vacío
- Verificar stock usable (libre utilización, control calidad)
- Verificar cruce MB52 ↔ Visión
- Verificar maestro de materiales (familias + tiempos)

### 6.2 "Familia no configurada en ninguna línea"
- Asignar familia en Config > Familias
- Habilitar familia en líneas del proceso

### 6.3 KPI histórico no aparece
- Requiere snapshots de Visión Planta (se guarda al importar)
