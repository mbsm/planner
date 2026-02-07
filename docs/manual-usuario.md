# Foundry Plan — Manual de Usuario

Este documento guía al usuario final sobre cómo operar el sistema **Foundry Plan** para planificar y despachar producción.

## 1. Visión General
Foundry Plan ayuda a secuenciar la producción diaria, permitiendo cargar el stock y demanda desde SAP y generando colas de trabajo priorizadas por fecha de entrega y restricciones técnicas.

La aplicación funciona en tu navegador web.

## 2. Iniciar la aplicación
Si la aplicación no está corriendo en un servidor, ejecútela localmente:
1. Abra una terminal en la carpeta del proyecto.
2. Ejecute `run_app.py --port 8080` (o el script de inicio provisto por TI).
3. Abra su navegador en `http://localhost:8080`.

## 3. Flujo de Trabajo Semanal/Diario

Se recomienda seguir este orden de operaciones:

### Paso 1: Carga de Datos (Menú "Actualizar")
El sistema requiere archivos Excel exportados desde SAP para funcionar.
**Nota:** Cada carga **reemplaza** completamente la información anterior del mismo tipo.

1.  **Vaya a la página "Actualizar"**.
2.  **Verifique la configuración**: Asegúrese que el Centro y Almacén mostrados corresponden a su área (ej: `4000` / `4035`).
3.  **Subir MB52 (Stock)**:
    - Exporte la transacción MB52 desde SAP a Excel (`.xlsx`).
    - Arrastre el archivo al área "Cargar MB52".
    - El sistema filtrará automáticamente según configuración de cada proceso.
4.  **Subir Visión Planta (Pedidos)**:
    - Exporte el reporte de Visión Planta (ZPP_VISION...) a Excel.
    - Arrastre el archivo al área "Cargar Visión Planta".
    - Esta es la fuente oficial de las fechas de entrega (`Fecha de pedido`) y cantidades pendientes.
    - **Filtrado automático**: Solo se importan productos finales (Piezas: 40XX00YYYYY) con aleaciones configuradas.
5.  **Subir Desmoldeo (Opcional - Para Planner)**:
    - Exporte el reporte de Desmoldeo desde SAP a Excel.
    - Arrastre el archivo al área "Cargar Desmoldeo".
    - Define moldes en enfriamiento y flasks ocupadas.
    - Se divide automáticamente en:
      - **Moldes por Fundir**: Sin fecha de desmoldeo (WIP)
      - **Piezas Fundidas**: Con fecha de desmoldeo (completadas)

**Importante:**
- El sistema cruza MB52 y Visión usando "Pedido" y "Posición".
- Los materiales nuevos generan alerta para clasificación en el maestro.
- El desmoldeo actualiza automáticamente tiempos de enfriamiento y piezas por molde en el maestro.

### Paso 2: Gestión de Maestro de Materiales
Si al cargar datos aparecen materiales nuevos, verá una ventana emergente o alerta en la página "Config > Maestro de Materiales".

Debe completar para cada material nuevo:
- **Familia**: Define en qué líneas puede fabricarse.
- **Tiempos de Proceso (días)**: Vulcanizado, Mecanizado, Inspección, etc. Esto afecta la fecha de inicio sugerida.
- **Peso Neto (Net Weight)**: Revisar que sea correcto (viene de Visión, pero se puede ajustar).
- **Parámetros de Moldeo**: Aleación, Piezas por molde, etc.

*Nota:* Si no clasifica un material, el sistema no podrá programarlo en ninguna línea.

### Paso 3: Revisión de Pedidos (Menú "Pedidos")
En esta pantalla verá un resumen del estado de la planta:
- **KPIs**: Toneladas atrasadas y órdenes críticas.
- **Lista de Pedidos**: Busque pedidos específicos, vea su estado de avance y stock.
- **Prioridad**: Puede marcar manualmente pedidos como "Prioritarios" (casilla de verificación). Esto forzará al sistema a programarlos antes.

### Paso 4: Planificación de Moldeo (Menú "Plan")
Utilice esta herramienta para definir cuántos moldes fabricar por día.

**Configuración y Parámetros:**
1. **Capacidades (Config > Planner)**:
   - **Moldes por turno**: Capacidad nominal de moldeo
   - **Mismo material/día**: Máximo del mismo material que se puede moldear en un día
   - **Toneladas fusión por turno**: Capacidad de colada
   - **Turnos por día**: Configurables por día de semana (lun-dom)

2. **Algoritmo de Placement (Config > Planner)**:
   - **Búsqueda máxima (días)**: Máximo número de días hacia adelante para buscar ventanas válidas (default: 365)
   - **Permitir huecos en moldeo**: Si se activa, permite moldear en días no consecutivos para un mismo pedido (default: No)

3. **Cajas (Flasks)**:
   - Configure inventario total por tipo de caja (ej: 105, 120, 143)
   - El sistema descuenta automáticamente cajas ocupadas desde desmoldeo

**Funcionamiento:**
- El planner propone un plan basado en:
  - Fechas de entrega (due_date)
  - Capacidad de moldeo diaria
  - Disponibilidad de flasks por tipo
  - Capacidad de colada (metal)
  - Tiempos de enfriamiento
- **Optimización automática**: El sistema puede reducir tiempos de terminación (finish_days) hasta el mínimo configurado (min_finish_days) para cumplir fechas de entrega
- **Búsqueda de ventanas**: El algoritmo busca la primera ventana viable desde hoy, respetando contiguidad (si está configurado)

**Condiciones Iniciales:**
- Vista de flasks ocupadas por tipo de caja
- Moldes en cancha (WIP desde desmoldeo)
- Toneladas pendientes de fundir

### Paso 5: Programación de Despacho (Menú "Programa")
Genera las colas de trabajo para las líneas productivas (Terminaciones, Mecanizado, etc.).
- **Seleccione el Proceso**: En la barra superior (ej: "Terminacion").
- **Colas por Línea**: Verá la secuencia sugerida de trabajos para cada máquina/línea.
- **Ordenamiento Automático**:
    1.  Pruebas (Lotes con letras).
    2.  Prioridades manuales.
    3.  Fecha de entrega (calculada restando los tiempos de procesos posteriores).
- **Acciones**:
    - **Fijar (Pin)**: Haga clic en el ícono de "chinche" para fijar un trabajo en "Proceso". Esto evita que se mueva en futuras reprogramaciones automáticas.
    - **Detalle**: Doble clic para ver detalles del pedido.

## 4. Configuración
En el menú "Config" puede ajustar los parámetros operativos de la planta:

- **Procesos y Líneas**: Defina qué líneas existen y qué familias pueden procesar.
- **Familias**: Cree o edite agrupaciones de materiales.
- **Maestro de Materiales**: Edición masiva de tiempos y atributos de partes.
  - **Tiempos downstream** (vulcanizado, mecanizado, inspección): Solo para cálculo de start_by en dispatcher, **NO** afectan planner
  - **Tiempos de moldeo** (finish_days, min_finish_days, tiempo_enfriamiento_molde_horas): Usados por planner
  - **finish_days → min_finish_days**: El planner puede comprimir tiempos de terminación hasta el mínimo para cumplir fechas
- **Planner (Moldeo)**:
  - **Capacidades**: Moldes por turno, mismo material/día, toneladas fusión
  - **Turnos por día**: Configurables lun-dom para moldeo y fusión
  - **Algoritmo de Placement**:
    - **Búsqueda máxima (días)**: Rango de búsqueda para ventanas de moldeo (30-730 días, default: 365)
    - **Permitir huecos en moldeo**: Si activo, permite moldear en días no consecutivos (default: desactivado)
  - **Cajas (Flasks)**: Inventario total por tipo de caja
  - **Horizonte**: Días de planificación hacia adelante
  - **Feriados**: Fechas no laborables
- **Filtros de Disponibilidad**: Controla qué stock se considera "disponible" para cada proceso
  - Terminaciones: Solo stock libre y sin QC
  - Toma de dureza: Solo stock bloqueado o en QC

## 5. Preguntas Frecuentes

**¿Por qué un pedido no aparece en el Programa?**
- Verifique que el material tenga asignada una **Familia**.
- Verifique que exista una **Línea** configurada para aceptar esa Familia.
- Verifique que el stock esté en el almacén correcto (MB52) y cumpla los filtros de disponibilidad del proceso.

**¿Cómo maneja el sistema las "Pruebas"?**
- Cualquier lote que contenga letras (alfanumérico) se considera automáticamente una Prueba ("Test").
- Las pruebas tienen la prioridad más alta en la cola, superior a la fecha.

**¿Qué fecha usa el sistema para ordenar?**
- Usa la `Fecha de pedido` (fecha comprometida con el cliente) del archivo Visión Planta.
- Resta los días de `vulcanizado`, `mecanizado`, etc. configurados en el maestro para calcular cuándo debe comenzar el trabajo (start_by).

**¿Qué son vulcanizado_dias, mecanizado_dias e inspeccion_externa_dias?**
- Son tiempos de procesos **downstream** (después de moldeo).
- Solo se usan en el **Dispatcher** para calcular cuándo debe iniciar el trabajo (start_by).
- **NO** afectan las restricciones ni fechas del **Planner** (moldeo).

**¿Cómo funciona la optimización de tiempos de terminación?**
- Cada material tiene configurado:
  - `finish_days`: Tiempo nominal de terminación (ej: 15 días)
  - `min_finish_days`: Tiempo mínimo permitido (ej: 5 días)
- Si una orden se pasaría de la fecha de entrega con el tiempo nominal, el planner lo reduce hasta el mínimo.
- Ejemplo: Orden con due_date en 10 días, finish_days=15, min_finish_days=5:
  - El planner usará 10 días de finishing para cumplir la fecha.

**¿Qué es "Búsqueda máxima (días)" en el Planner?**
- Define hasta cuántos días hacia el futuro el algoritmo busca ventanas válidas para moldear.
- Default: 365 días.
- Si una orden no cabe en los primeros N días, el sistema intenta día a día hasta el límite.
- Aumentar este valor permite encontrar ventanas más lejanas pero puede incrementar tiempo de cálculo.

**¿Qué significa "Permitir huecos en moldeo"?**
- **Desactivado (default)**: El moldeo de una orden debe ser en días consecutivos.
- **Activado**: Permite moldear en días no consecutivos cuando no hay capacidad continua disponible.
- Recomendación: Mantener desactivado para simplicidad operativa.
