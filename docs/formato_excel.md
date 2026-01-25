# Formato Excel (SAP)

FoundryPlanner importa **2 archivos Excel** en `.xlsx` leyendo **solo la primera hoja**:

1. **MB52** (stock por pieza/lote)
2. **Visión Planta** (pedido/posición, fechas y pesos)

La app normaliza encabezados a un formato interno (minúsculas, sin acentos, espacios→`_`). Por eso, abajo se listan los nombres **internos** esperados; el archivo puede tener variantes (p. ej. `Pos.` / `Posición`), mientras el normalizador los deje equivalentes.

## 1) MB52 (stock)

Columnas requeridas (internas):

| Columna | Tipo | Ejemplo | Notas |
|---|---:|---|---|
| `material` | texto | `43633021531` | Número de parte |
| `centro` | texto/num | `4000` | Se normaliza si Excel lo convierte a `4000.0` |
| `almacen` | texto/num | `4035` | Ídem |
| `lote` | texto | `001-002` / `0030PD0674` | Lote/correlativo por pieza; puede ser alfanumérico |
| `libre_utilizacion` | 0/1 | `1` | Usable=1 |
| `en_control_calidad` | 0/1 | `0` | Usable=0 |
| `documento_comercial` | texto/num | `1010044531` | Pedido de venta (SAP) |
| `posicion_sd` | texto/num | `10` | Posición (SAP) |

Columnas opcionales útiles:
- `texto_breve_de_material` o `texto_breve`

Reglas clave:
- Se consideran piezas “usables” cuando se cumple `libre_utilizacion=1` y `en_control_calidad=0`.
- Para Terminaciones, lotes alfanuméricos (contienen letras) se consideran **pruebas** y se priorizan.
- El correlativo numérico se obtiene desde el **prefijo numérico** del lote (primer grupo de dígitos).

## 2) Visión Planta

Columnas requeridas (internas):

| Columna | Tipo | Ejemplo | Notas |
|---|---:|---|---|
| `pedido` | texto/num | `1010044531` | Debe cruzar con MB52 `documento_comercial` |
| `posicion` | texto/num | `10` | Debe cruzar con MB52 `posicion_sd` |
| `cod_material` | texto | `43633021531` | Referencial (la orden se arma desde MB52) |
| `fecha_de_pedido` | fecha | `2026-01-20` | Fecha base usada para planificar (se parsea desde Excel) |

Columnas opcionales (mejoran KPI y UI):

| Columna | Tipo | Ejemplo | Uso |
|---|---:|---|---|
| `fecha_entrega` | fecha | `2026-02-10` | Para cards Home (atrasados / próximas 2 semanas) |
| `solicitado` | entero | `120` | Para calcular pendientes vs bodega/despachado |
| `bodega` | entero | `10` | Progreso (pendientes = solicitado - bodega - despachado) |
| `despachado` | entero | `20` | Progreso |
| `peso_neto` | número | `12500` | Viene en **kg**; la app lo guarda en **tons** (kg/1000) |
| `cliente` | texto | `ACME` | UI |
| `n_oc_cliente` | texto | `OC-123` | UI |
| `descripcion_material` | texto | `PARRILLA ...` | UI |

Notas:
- La app calcula y guarda `peso_unitario_ton` como `peso_neto_ton / solicitado` cuando ambas existen.
- Si `fecha_entrega` no está, los cards de Home (atrasados / próximas 2 semanas) no se podrán poblar.

