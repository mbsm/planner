# 📋 DECISIONES TOMADAS - 30 de Enero 2026

Resumen de decisiones y cambios de documentación realizados en esta sesión de audit.

---

## ✅ DECISIÓN 1: Eliminar Progress Reports

**Decisión:** NO implementar `mb52_progress_last` ni `vision_progress_last`

**Racional:**
- Estos reportes se usaban para auditar "salidas" (piezas que desaparecieron)
- Son "nice-to-have" pero no críticos para MVP
- Simplifican la arquitectura

**Acción:**
- ❌ Eliminar tablas `mb52_progress_last`, `vision_progress_last` del plan
- ✅ Actualizar plan de implementación
- ✅ Eliminar del audit

**Documentos actualizados:**
- `docs/PLAN_IMPLEMENTACION.md` - No incluye estas tablas
- `PROGRESS.md` - No incluye estas sub-fases

---

## ✅ DECISIÓN 2: Mantener vision_kpi_daily para Gráfico de Atrasos

**Decisión:** MANTENER e IMPLEMENTAR `vision_kpi_daily`

**Racional:**
- Crítica para el gráfico histórico de KPIs en Home/Pedidos (especificacion.md línea 19)
- Usa snapshots diarios de tons_por_entregar + tons_atrasadas
- Simple de implementar (upsert diario)

**Acción:**
- ✅ Incluir en FASE 1.6 (Auditoría & KPI)
- ✅ Marcar como CRÍTICA para Home

**Documentos actualizados:**
- `docs/PLAN_IMPLEMENTACION.md` - Sección 1.6
- `PROGRESS.md` - Bloqueador conocido

**Métodos públicos requeridos:**
```python
upsert_vision_kpi_daily(snapshot_date=None) → dict
get_vision_kpi_daily_rows(limit=120) → list[dict]
```

---

## ✅ DECISIÓN 3: Auto-detection de Tests es Protegido

**Decisión:** VALIDAR e IMPLEMENTAR correctamente

**Racional:**
- Tests alfanuméricos se detectan automáticamente en `get_test_orderpos_set()`
- Se marcan con `kind='test'` en `orderpos_priority`
- **NO se pueden desmarcar** (`delete_all_pedido_priorities(keep_tests=True)`)

**Acción:**
- ✅ Documentar en plan que esto NO es opcional
- ✅ Validar en tests que los tests no se pueden desmarcar
- ✅ Agregación: Este comportamiento ya está en especificacion.md

**Documentos actualizados:**
- `docs/PLAN_IMPLEMENTACION.md` - Sección 2.3, **Nota importante**
- `PROGRESS.md` - Marcado como IMPORTANTE

**Código relevante:**
```python
# En delete_all_pedido_priorities:
WHERE COALESCE(kind,'') <> 'test'  # ← Tests se preservan
```

---

## ✅ DECISIÓN 4: Peso Automático Ya Está Documentado

**Decisión:** NO hacer cambios - Ya está correcto

**Verificación:**
- especificacion.md línea 97: "peso_unitario_ton se actualiza desde Visión"
- modelo-datos.md: referencias al peso_neto y conversión kg→tons
- Código: `import_sap_vision_bytes()` realiza actualización automática

**Acción:**
- ✅ NO incluir como cambio necesario
- ✅ Incluir en plan como ya implementado
- ✅ Validar que comentarios en código coinciden con documentación

**Documentos actualizados:**
- `docs/PLAN_IMPLEMENTACION.md` - Sección 2.2, referencia a lo ya hecho
- `PROGRESS.md` - No es un bloqueador

---

## ✅ DECISIÓN 5: Lowest-Qty Split Distribution - Revisar Implementación

**Decisión:** REVISAR si está implementado según spec

**Racional:**
- especificacion.md línea 376: "asigna al split con menor cantidad"
- Código (`repository.py` línea 2717): parece asignar al último split
- Esto debe validarse antes de marcar como "done"

**Acción:**
- ⏳ **PENDIENTE:** Code review de `_apply_in_progress_locks()`
- ⏳ **PENDIENTE:** Si no está implementado, agregar cambio en próximo sprint

**Documentos actualizados:**
- `docs/PLAN_IMPLEMENTACION.md` - Sección 3.2, ⚠️ **Nota**
- `PROGRESS.md` - Bloqueador conocido

---

## ✅ DECISIÓN 6: Eliminar Archivos de Audit

**Decisión:** Limpiar proyecto, mantener solo plan de implementación

**Archivos eliminados:**
- ❌ `AUDIT_SUMMARY.md`
- ❌ `AUDIT_CODE_VS_DOCS.md`
- ❌ `AUDIT_QUICK_REFERENCE.md`
- ❌ `DOCUMENTATION_CHECKLIST.md`
- ❌ `DOCUMENTATION_UPDATE_PLAN.md`
- ❌ `docs/implementado.md`
- ❌ `docs/estado.md`

**Razón:**
- Audit completado y accionado
- Plan de implementación reemplaza estos

**Acción:**
- ✅ Ejecutado

---

## ✅ DECISIÓN 7: Crear Plan Ejecutable con Checkboxes

**Decisión:** Nuevo enfoque para seguimiento

**Archivos creados:**
- ✅ `docs/PLAN_IMPLEMENTACION.md` - Plan detallado con checkboxes
- ✅ `PROGRESS.md` - Dashboard rápido de progreso

**Beneficios:**
- Fácil de usar y actualizar
- Visible en raíz del proyecto (`PROGRESS.md`)
- Checkboxes permiten marcar completion
- Bloqueadores identificados

---

## 📊 RESUMEN DE CAMBIOS

| Aspecto | Antes | Ahora | Estado |
|---|---|---|---|
| Audit | 5 archivos | 0 archivos | ✅ Limpio |
| Plan | 0 documentos | 2 documentos | ✅ Ejecutable |
| Decisiones | Implícitas | Explícitas | ✅ Documentadas |
| Progress Tracking | Manual | Dashboard + Checkboxes | ✅ Sistemático |

---

## 🎯 PRÓXIMOS PASOS

1. **Inmediato:**
   - [ ] Revisar `PROGRESS.md` en raíz
   - [ ] Revisar `docs/PLAN_IMPLEMENTACION.md` para contexto
   - [ ] Comenzar FASE 1 (Tablas & Persistencia)

2. **Esta semana:**
   - [ ] Completar FASE 1
   - [ ] Completar FASE 2 (Import)
   - [ ] Comenzar FASE 3 (Job Calculation)

3. **Próxima semana:**
   - [ ] Completar FASE 3-4 (Dispatcher)
   - [ ] Comenzar FASE 5 (Persistencia)
   - [ ] Comenzar FASE 6 (UI)

4. **Bloqueador a resolver:**
   - [ ] Code review: Split distribution (lowest-qty vs last)
   - [ ] Decidir si implementar o cambiar spec

---

## 📞 REFERENCIAS

| Documento | Propósito |
|---|---|
| `docs/especificacion.md` | Fuente de verdad (requerimientos) |
| `docs/modelo-datos.md` | Estructura de BD (schema) |
| `docs/jobs-dispatch-architecture.md` | Flujos y arquitectura |
| `docs/PLAN_IMPLEMENTACION.md` | Plan detallado (este) |
| `PROGRESS.md` | Dashboard de progreso |

---

## ✏️ NOTAS IMPORTANTES

- **Vision_kpi_daily es CRÍTICA:** Sin ella, Home no tiene gráfico de atrasos
- **Tests no se pueden desmarcar:** Validar que UI lo respeta
- **Auto-detection es automático:** Busca regex `[A-Za-z]` en lotes MB52
- **Peso es automático:** Se actualiza desde Visión sin intervención usuario
- **Split distribution:** Requiere code review antes de marcar como done

---

**Documento creado:** 30 de enero 2026, 17:30  
**Por:** Audit & Plan Session  
**Versión:** v0.2

