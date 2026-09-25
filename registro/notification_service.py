"""
registro/notification_service.py
Despacho omnicanal asíncrono de recibos y facturas por WhatsApp (Meta Cloud API) y Correo Electrónico.
Integra la captura de clientes para el CRM Popular (Módulo 9) y el vCard descargable para Estados.
"""
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple
from django.utils import timezone
from django.conf import settings

from .models import Venta, Cliente, Establecimiento


def generar_payload_whatsapp_utility(
    venta: Venta,
    telefono_cliente: str,
    nombre_cliente: str = "Vecino",
    base_url_recibo: str = "https://recibo.diariocomercial.co",
) -> Dict[str, Any]:
    """
    Construye el payload oficial de categoría UTILITY para Meta Cloud API.
    Cumple al 100% las directivas de WhatsApp para no ser clasificado como spam.
    """
    tel_limpio = "".join(c for c in telefono_cliente if c.isdigit())
    if len(tel_limpio) == 10:
        tel_limpio = f"57{tel_limpio}" # Código país Colombia

    url_recibo = f"{base_url_recibo}/r/{venta.pk}/"
    valor_fmt = f"${venta.valor:,.0f}".replace(",", ".")
    tienda = venta.establecimiento.nombre

    return {
        "messaging_product": "whatsapp",
        "to": tel_limpio,
        "type": "template",
        "template": {
            "name": "recibo_venta_diariocomercial_v1",
            "language": {"code": "es"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": nombre_cliente},
                        {"type": "text", "text": tienda},
                        {"type": "text", "text": str(venta.pk)},
                        {"type": "text", "text": valor_fmt},
                        {"type": "text", "text": url_recibo},
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": [
                        {"type": "text", "text": str(venta.pk)},
                    ],
                },
            ],
        },
    }


def registrar_o_actualizar_cliente_desde_venta(
    establecimiento: Establecimiento,
    telefono: str,
    nombre: str = "",
    direccion: str = "",
    punto_referencia: str = "",
    nit_cedula: str = "",
) -> Optional[Cliente]:
    """
    Captura de Leads del Barrio: Cada vez que se envía un recibo digital, el cliente
    queda registrado en el CRM de la tienda sin fricción para futuras compras y fiados.
    """
    tel_limpio = "".join(c for c in telefono if c.isdigit())
    if not tel_limpio:
        return None

    cliente, creado = Cliente.objects.get_or_create(
        establecimiento=establecimiento,
        telefono=tel_limpio,
        defaults={
            "nombre": nombre.strip() or f"Cliente {tel_limpio[-4:]}",
            "direccion": direccion.strip(),
            "punto_referencia": punto_referencia.strip(),
            "nit_cedula": nit_cedula.strip(),
            "fecha_ultimo_pedido": timezone.now(),
        },
    )

    if not creado:
        if nombre and cliente.nombre.startswith("Cliente "):
            cliente.nombre = nombre.strip()
        if direccion and not cliente.direccion:
            cliente.direccion = direccion.strip()
        if punto_referencia and not cliente.punto_referencia:
            cliente.punto_referencia = punto_referencia.strip()
        cliente.fecha_ultimo_pedido = timezone.now()
        cliente.save()

    return cliente
