# 🕵️ Shadow Tax Auditing — DiarioComercial

> **Rama de Exploración y Desarrollo:** `dev/shadow-tax-auditing`  
> **Objetivo:** Captura, cálculo fiscal y auditoría tributaria en tiempo real en paralelo a la venta en un software de caja principal (POS / ERP), sin bloquear ni demorar la facturación.

---

## 🎯 ¿Qué es "Shadow Tax Auditing"?

En los comercios tradicionales de Tunja, el dependiente o cajero registra ventas a alta velocidad en su sistema POS (caja registradora). 

Muchas veces, el cálculo de tributos (ICA municipal, retenciones, topes) se hace semanas después cuando el contador recibe papeles desordenados.

**Shadow Tax Auditing** resuelve esto operando "en la sombra" (shadow mode):
1. **Emisión no intrusiva:** Cada vez que el POS imprime o guarda una factura, envía una copia ligera del evento JSON en segundo plano (webhook / API).
2. **Cálculo instantáneo:** DiarioComercial recibe el evento, aplica la tarifa CIIU correspondiente (x mil de Tunja), evalúa si hubo retención practicada por empresas y estima el ICA devengado.
3. **Alertas tempranas:** Si una venta supera topes o presenta inconsistencias de retención, genera una bandera de auditoría sin detener la operación de caja.
4. **Trazabilidad total:** Queda registrado en el libro de auditoría con la etiqueta `shadow_audit` y el número de ticket original del POS.

---

## 🏗️ Arquitectura de la Solución

```
   [ Software POS Principal ]
   (Cajero vendiendo en mostrador)
                 │
                 │ 1. Evento HTTP POST asíncrono
                 ▼
 [ Webhook /api/shadow-audit/ingest/ ]
                 │
                 │ 2. ShadowTaxAuditor (auditor.py)
                 ├───────────────────────────────┐
                 ▼                               ▼
       [ Base de Datos ICA ]             [ Alertas y Auditoría ]
   - Venta clasificada por CIIU      - Verificación de topes
   - ICA estimado devengado          - Cruce de ReteICA con terceros
   - Trazabilidad de cajero          - Registro inmutable en Auditoria
```

---

## 🚀 Cómo probarlo en esta rama

Puedes ejecutar el simulador incluido que recrea una caja registradora emitiendo ventas reales:

```powershell
.\.venv\Scripts\python.exe shadow_tax_audit\simulador_pos.py
```

Luego puedes abrir DiarioComercial en [http://127.0.0.1:8001/historial/](http://127.0.0.1:8001/historial/) o [http://127.0.0.1:8001/auditoria/](http://127.0.0.1:8001/auditoria/) para ver cómo se reflejaron los tickets y las banderas de auditoría.
