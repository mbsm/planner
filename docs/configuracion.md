# Configuración

PlannerTerm guarda parámetros en SQLite y se administran desde **Config** dentro de la app.

## Parámetros SAP

- `sap_centro`
  - Centro SAP (por defecto: `4000`).
- `sap_material_prefixes`
  - Prefijos de materiales a mantener desde MB52.
  - Ejemplo: `436`.
  - Para mantener todo: `*`.

## Almacenes por proceso

Cada proceso usa un almacén diferente (según configuración en la app). Para Terminaciones se usa:

- `sap_almacen_terminaciones` (por defecto: `4035`)

La app también expone procesos adicionales en el menú **Programas Producción** (mecanizado, inspección externa, etc.), cada uno con su propio almacén asociado.

## Reglas relevantes

- **Piezas usables (MB52)**: `libre_utilizacion=1` y `en_control_calidad=0`.
- **Pruebas (Terminaciones)**: lote alfanumérico (contiene letras) → prioridad automática.
- **Correlativo desde lote**: se toma el **prefijo numérico** (primer grupo de dígitos).

## UI

- `ui_allow_move_in_progress_line`
  - `1` habilita mover una orden marcada “en proceso” a otra línea desde el diálogo de la tabla.
