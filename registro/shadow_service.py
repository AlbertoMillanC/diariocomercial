"""
registro/shadow_service.py
Módulo Shadow (Modo Sombra): Intercepta, decodifica y sincroniza transacciones de software POS
o contable existente (SIIGO, Servinte, POS Windows, Access, FoxPro) sin cambiar el sistema del cliente.

Flujo:
1. El agente local 'diario-shadow-agent' captura los bytes del spooler de impresión o archivo de caja.
2. Este servicio parsea el texto en <15 ms, extrae montos y conceptos.
3. Asienta la venta en DiarioComercial para alimentar en segundo plano el ICA, la Exógena y Bre-B.
"""
import re
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from .models import Establecimiento, Venta, ActividadCIIU
from .inventario_service import normalizar_texto, parsear_dinero
from .tax_engine import registrar_evento_exogena


def parsear_ticket_impresion_legacy(texto_impresion: str) -> Dict[str, Any]:
    """
    Parsea el contenido de un ticket de caja o tirilla térmica de software antiguo.
    Extrae el total, subtotal, líneas de productos y posibles datos de tercero/NIT.
    """
    lineas = [l.strip() for l in (texto_impresion or "").splitlines() if l.strip()]
    total_encontrado = None
    items = []
    nit_cliente = ""
    nombre_cliente = ""

    for l in lineas:
        l_norm = normalizar_texto(l)

        # 1. Búsqueda de línea de TOTAL
        if any(kw in l_norm for kw in ["total", "total a pagar", "total venta", "valor total"]):
            val, _ = parsear_dinero(l)
            if val and val > 0:
                total_encontrado = val

        # 2. Detección de cliente / NIT
        if "nit" in l_norm or "c.c" in l_norm or "cedula" in l_norm or "cliente" in l_norm:
            m_nit = re.search(r"\b(\d{6,12}(?:-\d)?)\b", l)
            if m_nit:
                nit_cliente = m_nit.group(1)

        # 3. Detección de ítems de venta (descripción + valor al final)
        m_item = re.search(r"^(.*?)(?:\$|\s{2,})(\d{1,3}(?:\.\d{3})+|\d{3,})$", l)
        if m_item and "total" not in l_norm and "subtotal" not in l_norm:
            desc = m_item.group(1).strip()
            precio_raw = m_item.group(2).replace(".", "").replace(",", "")
            if desc and precio_raw.isdigit():
                items.append({
                    "descripcion": desc,
                    "valor": Decimal(precio_raw),
                })

    # Si no se encontró línea explícita de total pero hay items, sumar los items
    if not total_encontrado and items:
        total_encontrado = sum([it["valor"] for it in items], Decimal("0"))

    return {
        "total": total_encontrado or Decimal("0"),
        "items": items,
        "nit_cliente": nit_cliente,
        "conteo_items": len(items),
    }


def ingestar_venta_shadow(
    establecimiento: Establecimiento,
    texto_o_payload_ticket: str,
    medio_pago_detectado: str = "efectivo",
    id_externo_ticket: str = "",
) -> Tuple[bool, Optional[Venta], str]:
    """
    Ingesta una venta interceptada en segundo plano por el Módulo Shadow.
    Alimenta el Libro Diario, el cálculo del ICA y la Exógena Municipal.
    """
    datos = parsear_ticket_impresion_legacy(texto_o_payload_ticket)
    total = datos["total"]

    if total <= Decimal("0"):
        return False, None, "No se pudo extraer un total válido del ticket interceptado."

    usuario = establecimiento.perfil_set.first().user if establecimiento.perfil_set.exists() else None
    actividad = establecimiento.actividades.first()

    concepto_resumen = f"Shadow POS #{id_externo_ticket}" if id_externo_ticket else "Venta Interceptada Shadow"
    if datos["items"]:
        concepto_resumen = ", ".join([it["descripcion"] for it in datos["items"][:2]])[:120]

    with transaction.atomic():
        venta = Venta.objects.create(
            establecimiento=establecimiento,
            usuario=usuario,
            actividad=actividad,
            fecha=timezone.localdate(),
            fecha_hora=timezone.now(),
            valor=total,
            concepto=concepto_resumen,
            observacion=f"Interceptado por Módulo Shadow (Ref: {id_externo_ticket or 'spooler'})",
            medio_pago=medio_pago_detectado,
            tipo_cliente="empresa" if datos["nit_cliente"] else "particular",
            estado="vigente",
        )

        # Si venía con NIT de cliente, registrar para Exógena Municipal
        if datos["nit_cliente"]:
            registrar_evento_exogena(
                establecimiento=establecimiento,
                tipo_registro="venta",
                nit_tercero=datos["nit_cliente"],
                nombre_razon_social="Cliente Interceptado Shadow",
                monto_base=total,
                tarifa_aplicada=actividad.tarifa_x_mil if actividad else Decimal("6.0"),
            )

    return True, venta, f"Venta Shadow #{venta.pk} procesada con éxito por ${total:,.0f}"
