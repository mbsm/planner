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
El sistema requiere dos archivos Excel exportados desde SAP para funcionar.
**Nota:** Cada carga **reemplaza** completamente la información anterior del mismo tipo.

1.  **Vaya a la página "Actualizar"**.
2.  **Verifique la configuración**: Asegúrese que el Centro y Almacén mostrados corresponden a su área (ej: `4000` / `4035`).
3.  **Subir MB52 (Stock)**:
    - Exporte la transacción MB52 desde SAP a Excel (`.xlsx`).
    - Arrastre el archivo al área "Cargar MB52".
    - El sistema filtrará automáticamente el stock "Libre utilización" y disponible.
4.  **Subir Visión Planta (Pedidos)**:
    - Exporte el reporte de Visión Planta (ZPP_VISION...) a Excel.
    - Arrastre el archivo al área "Cargar Visión Planta".
    - Esta es la fuente oficial de las fechas de entrega (`Fecha de pedido`) y cantidades pendientes.

**Importante:**
- El sistema cruza ambos archivos usando el número de "Pedido" y "Posición".
- Si un material aparece en MB52 o Visión pero no existe en el maestro local, el sistema le pedirá configurarlo inmediatamente.

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

### Paso 4: Planificación de Moldeo (Menú "Plan") - *Nuevo*
Utilice esta herramienta los días Lunes para definir cuántos moldes fabricar por día.
- El sistema propone un plan basado en la fecha de entrega y capacidad de moldeo.
- Puede revisar y confirmar el plan semanal.

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
- **Planner**: Configuración de capacidades diarias de moldeo y tamaños de cajas (flasks).

## 5. Preguntas Frecuentes

**¿Por qué un pedido no aparece en el Programa?**
- Verifique que el material tenga asignada una **Familia**.
- Verifique que exista una **Línea** configurada para aceptar esa Familia.
- Verifique que el stock esté en el almacén correcto (MB52) y tenga estatus "Libre utilización".

**¿Cómo maneja el sistema las "Pruebas"?**
- Cualquier lote que contenga letras (alfanumérico) se consiera automáticamente una Prueba ("Test").
- Las pruebas tienen la prioridad más alta en la cola, superior a la fecha.

**¿Qué fecha usa el sistema para ordenar?**
- Usa la `Fecha de pedido` (fecha comprometida con el cliente) del archivo Visión Planta.
- Resta los días de `vulcanizado`, `mecanizado`, etc. configurados en el maestro para calcular cuándo debe comenzar el trabajo.
