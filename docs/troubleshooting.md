# Troubleshooting

## No aparecen pedidos / programa vacío

- Verifica en **Actualizar**:
  - `sap_centro` y almacén del proceso (Terminaciones por defecto: 4035)
  - Se hayan subido ambos archivos: **MB52** y **Visión Planta**
- Revisa que MB52 tenga piezas “usables”:
  - `libre_utilizacion=1`
  - `en_control_calidad=0`
  - `documento_comercial`, `posicion_sd` y `lote` no vacíos

## “Familia no configurada en ninguna línea”

- Asigna familia a los materiales en **Config > Familias**.
- Asegura que esa familia esté habilitada en al menos una línea (Config de líneas).

## Avance Producción no muestra datos

- El reporte se genera al subir **MB52** en modo **replace**.
- Requiere que exista un **último programa** para Terminaciones.
- El avance usa “salidas brutas”: ítems que estaban en MB52 anterior y ya no están en el actual.

## KPI histórico (tons) no aparece

- Se guarda un snapshot diario al subir **Visión Planta**.
- Si aún no subes Visión, el gráfico histórico queda vacío.

## Problemas para push a GitHub

- Si no tienes SSH keys configuradas, usa remoto HTTPS:
  - `git remote set-url origin https://github.com/<owner>/<repo>.git`
  - `git push -u origin main`
