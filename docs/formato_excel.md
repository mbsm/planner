# Formato Excel (v1)

El sistema importa **1 archivo Excel** en `.xlsx` leyendo **la primera hoja**.

## 1) Pedidos (hoja principal)

Una fila = **1 pedido** y por definición **1 pedido corresponde a 1 número de parte**.

Columnas requeridas:

| Columna | Tipo | Ejemplo | Notas |
|---|---:|---|---|
| `pedido` | texto | `4500123456` | Identificador del pedido (SAP) |
| `numero_parte` | texto | `PZ-1234` | Número de parte |
| `cantidad` | entero | `120` | Cantidad total a fabricar/procesar |
| `fecha_entrega` | fecha | `2026-01-20` | Puede venir como fecha Excel o texto ISO |
| `primer_correlativo` | entero | `1001` | Inicio del rango correlativo del pedido |
| `ultimo_correlativo` | entero | `1120` | Fin del rango correlativo del pedido |

Aliases soportados (por compatibilidad):
- `corr_inicio` → `primer_correlativo`
- `corr_fin` → `ultimo_correlativo`

Columnas opcionales:

| Columna | Tipo | Ejemplo | Uso |
|---|---:|---|---|
| `tiempo_proceso_min` | número | `35.5` | Para una v2 (cálculo de “última fecha para partir”) |

Reglas de validación:
- Debe cumplirse: `ultimo_correlativo - primer_correlativo + 1 = cantidad`.

Fecha (`fecha_entrega`):
- Se acepta fecha Excel (numérica) o fecha tipo datetime.
- Se aceptan textos: `YYYY-MM-DD`, `DD-MM-YYYY`, `DD/MM/YYYY`, `YYYY/MM/DD`.

Ejemplo:

| pedido | numero_parte | cantidad | fecha_entrega | primer_correlativo | ultimo_correlativo | tiempo_proceso_min |
|---|---|---:|---|---:|---:|---:|
| 4500123456 | PZ-1234 | 120 | 2026-01-20 | 1001 | 1120 | 40 |

## Familias (maestro manual en la app)

La familia **no viene desde SAP** en esta versión; se administra en la app y se guarda en SQLite.

- Catálogo inicial sugerido: `Parrillas`, `Lifters`, `Corazas`, `Otros`.
- Cuando importas pedidos y hay números de parte sin familia definida, ve a **Config > Familias**.

## Tiempos de proceso (post-terminación)

Para priorización, la app usa:

$start\_by = fecha\_entrega - (vulcanizado\_dias + mecanizado\_dias + inspeccion\_externa\_dias)$

Estos tiempos se editan en **Config > Tiempos de proceso**.

## Notas
- En v1 el scheduler usa solo `fecha_entrega` + restricción de familia por línea.
- Los rangos de correlativos se muestran en el programa final para uso en terreno.
