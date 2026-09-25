# BITÁCORA TÉCNICA Y LOGS DE ARQUITECTURA TRIBUTARIA & MULTI-TENANT
## DiarioComercial: La Super-App del Comercio Popular Colombiano
**Versión:** 2.0 (Bre-B + Multi-Tenant + Modo Dual + Motor Tributario)  
**Fecha:** Septiembre de 2026  
**Autor:** Equipo de Ingeniería y Arquitectura  

---

## 📌 1. PROPÓSITO DE ESTE DOCUMENTO
Este archivo consolida la documentación viva, paso a paso, de las decisiones de ingeniería, modelos de datos, contratos de API, puntos de extensión futuros (especialmente **Exógena Municipal**) y guías de contingencia ante fallos en producción.

---

## 🏛️ 2. RESUMEN DE COMPONENTES IMPLEMENTADOS Y VERIFICADOS

```
┌────────────────────────────────────────────────────────────────────────┐
│              ESTADO DE EJECUCIÓN: 28 PRUEBAS APROBADAS (100% OK)       │
├────────────────────────────┬──────────────────┬────────────────────────┤
│ Módulo / Servicio          │ Archivo Código   │ Suite de Pruebas       │
├────────────────────────────┼──────────────────┼────────────────────────┤
│ Multi-Tenancy & Canales    │ models.py        │ test_fase1_fase2.py    │
│ Motor Desacoplado & RBAC   │ bot_service.py   │ test_fase1_fase2.py    │
│ Inventario Atómico         │ inventario_...py │ test_fase1_fase2.py    │
│ Bre-B (EMVCo & Webhook)    │ bre_b_service.py │ test_bre_b.py          │
│ ICA Tunja & Multi-Ciudad   │ tax_engine.py    │ test_tax_engine.py     │
│ Exógena Municipal Base     │ tax_engine.py    │ test_tax_engine.py     │
│ Impresión Térmica ESC/POS  │ print_service.py │ test_fase5.py          │
│ Módulo Shadow Interceptor  │ shadow_service.py│ test_fase5.py          │
│ Despacho WhatsApp & CRM    │ notif_service.py │ test_fase5.py          │
└────────────────────────────┴──────────────────┴────────────────────────┘
```

---

## ⚖️ 3. MOTOR TRIBUTARIO MULTI-MUNICIPIO (ICA)

### 3.1 El Caso Piloto: Tunja (Acuerdo 0032 de 2020)
El cálculo de la **Declaración Sugerida de ICA Tunja** implementado en `registro/tax_engine.py` sigue la siguiente fórmula matemática legal estricta:

$$\text{Base Gravable Neta} = \text{Ingresos Brutos} - (\text{Ingresos Fuera del Municipio} + \text{Devoluciones/Descuentos})$$

$$\text{Impuesto Neto ICA} = \sum \left( \frac{\text{Base Actividad} \times \text{Tarifa CIIU}}{1000} \right)$$

$$\text{Avisos y Tableros (Ley 97/1913)} = \text{Impuesto Neto ICA} \times 15\%$$

$$\text{Sobretasa Bomberil (Ley 1575/2012)} = \text{Impuesto Neto ICA} \times 5\%$$

$$\text{Total Impuesto a Cargo} = \text{Impuesto Neto ICA} + \text{Avisos y Tableros} + \text{Sobretasa Bomberil}$$

$$\text{Saldo Neto a Pagar} = \max(0, \text{Total Impuesto a Cargo} - \text{Retenciones ICA a Favor})$$

### 3.2 Extensión Paramétrica a Cualquier Municipio de Colombia
Para habilitar una nueva ciudad (ej. Duitama `15238`, Sogamoso `15759`, Bogotá `11001` o Medellín `05001`):
1. Insertar el municipio en la tabla `Municipio` con su código DANE oficial.
2. Crear un registro en `ReglaTributariaMunicipio`:
   - Configurar `porcentaje_avisos_y_tableros` (normalmente 15%).
   - Configurar `porcentaje_sobretasa_bomberil` (varía entre 4% y 7% según el acuerdo municipal).
   - Configurar `meses_periodo_ica` (2 para bimestral, 12 para anual).
   - Referenciar el número de Acuerdo Municipal vigente.
3. El motor `liquidar_declaracion_sugerida_ica` adopta automáticamente las reglas sin modificar una sola línea de código.

---

## 📑 4. MEDIOS MAGNÉTICOS MUNICIPALES (EXÓGENA MUNICIPAL)

### 4.1 Punto de Extensión para el Futuro (Future-Proof Design)
Las resoluciones de medios magnéticos municipales son expedidas anualmente por cada Secretaría de Hacienda (ej. Resolución de Exógena Tunja para el año gravable 2026).
Para garantizar que el sistema acumule la información desde el primer día sin necesidad de reprogramar:
* Cada transacción registrada mediante `registro/tax_engine.py::registrar_evento_exogena` almacena:
  - `nit_tercero`: Cédula o NIT del proveedor o cliente.
  - `nombre_razon_social`: Razón social completa.
  - `monto_base`: Valor antes de retención.
  - `monto_impuesto_retencion`: Retención practicada o asumida.
  - `tarifa_aplicada`: Tarifa por mil aplicada.
  - `año_gravable`: Año de causación.
  - `tipo_registro`: `compra`, `venta`, `reteica_practicado`, `reteica_asumido`.

### 4.2 Cómo Exportar el Archivo Plano Oficial
Cuando la Secretaría de Hacienda de Tunja publique la estructura técnica de columnas para el año respectivo (ej. Formato TXT delimitado por comas o plantilla Excel oficial):
1. Usar la función `generar_resumen_exogena_anual(establecimiento, año)`.
2. Mapear las columnas de `RegistroExogenaMunicipal.objects.filter(establecimiento=est, año_gravable=año)` al layout exigido por el portal de la Alcaldía.
3. Toda la información histórica ya está recolectada, limpia y clasificada por tercero.

---

## ⚡ 5. MOTOR BRE-B (ESTÁNDAR BANREP & WECHAT SOUNDBOX)

### 5.1 Especificación EMVCo MPM v1.0
Implementado en `registro/bre_b_service.py`:
* **Tag 00:** `01` (Versión).
* **Tag 01:** `12` (QR Dinámico con monto embebido).
* **Tag 26:** GUID `co.gov.banrep.bre-b` + Llave del comercio (`celular`, `nit`, `alias`).
* **Tag 54:** Monto exacto en COP (ej. `40000.00`).
* **Tag 62:** Sub-tag 05 con `referencia_unica` de transacción.
* **Tag 63:** Checksum CRC-16/CCITT-FALSE (polinomio `0x1021`, init `0xFFFF`).

### 5.2 Confirmación Anti-Fraude de Pantallazos Falsos:
* **Micro-token de 3 dígitos (ej: `#819`):** Se muestra en pantalla grande al momento del cobro.
* **Liquidación Atómica:** El dinero entra cuenta a cuenta (<2 segundos). El webhook firmado con HMAC-SHA256 dispara la creación de la venta, descuenta el inventario con `select_for_update()` y canta por voz en el parlante:
  > *"¡Bre-B recibido: $40.000 COP de Carlos Millán!"*

---

## 🥷 6. MÓDULO SHADOW (INTERCEPCIÓN DE POS EXISTENTE)

### 6.1 Cómo Funciona:
Para comercios que ya tienen software de facturación (SIIGO, POS Windows, software local):
1. El agente `diario-shadow-agent` se instala en el PC de caja como una impresora virtual de Windows.
2. Cuando el cajero imprime, el agente intercepta el texto plano en `<15 ms`.
3. `registro/shadow_service.py::parsear_ticket_impresion_legacy` extrae items, total y NIT.
4. `ingestar_venta_shadow` asienta la venta en DiarioComercial sin que el comerciante cambie su software habitual.

---

## 🖨️ 7. IMPRESIÓN ESC/POS & DESPACHO OMNICANAL

### 7.1 Impresión Térmica Zero-Click (`registro/print_service.py`):
* Genera flujo binario ESC/POS crudo (58mm / 80mm).
* Normalización ASCII para evitar ideogramas chinos en impresoras genéricas.
* Incluye corte total de papel (`GS V 66 0`).

### 7.2 WhatsApp Meta Cloud API (`registro/notification_service.py`):
* Plantilla aprobada de categoría `UTILITY` (`recibo_venta_diariocomercial_v1`).
* **Cero riesgo de baneo por spam.**
* Captura automática del cliente en la tabla `Cliente` (CRM Popular) con nombre, teléfono y punto de referencia para domicilios.

---

## 🛠️ 8. GUÍA DE RESOLUCIÓN DE INCIDENCIAS (TROUBLESHOOTING)

### Caso 1: Reintentos de Webhook Duplicados
* **Comportamiento:** La pasarela Bre-B reenvía el webhook por latencia de red.
* **Protección:** `TransaccionBreB.objects.select_for_update()`. Si el estado ya es `aprobada`, retorna `200 OK` inmediatamente con `"Transacción ya procesada previamente (Idempotente)"`.

### Caso 2: Intento de Acceso no Autorizado a Arqueos en Telegram
* **Comportamiento:** Un cajero dependiente escribe `/hoy` o `/consolidado`.
* **Protección:** `bot_service.despachar_mensaje` valida `perfil.es_propietario()`. Deniega el acceso con mensaje de advertencia y no expone cifras del negocio.

### Caso 3: Descuadre de Stock Concurrente
* **Comportamiento:** Dos cajeros venden el último kilo de carne al mismo milisegundo.
* **Protección:** `with transaction.atomic()` y `Producto.objects.select_for_update()`. La base de datos serializa las operaciones y el stock jamás cae en valores negativos inconsistentes.
