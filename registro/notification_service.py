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


def enviar_mensaje_whatsapp_meta(
    telefono_destinatario: str,
    texto_mensaje: str,
    token_acceso: str = "",
    phone_number_id: str = "",
) -> bool:
    """
    Envía un mensaje saliente a WhatsApp usando Meta Cloud API (v20.0).
    """
    import os
    import json
    import logging
    import urllib.request

    token = token_acceso or getattr(settings, "META_WHATSAPP_TOKEN", os.environ.get("META_WHATSAPP_TOKEN", ""))
    phone_id = phone_number_id or getattr(settings, "META_WHATSAPP_PHONE_ID", os.environ.get("META_WHATSAPP_PHONE_ID", ""))
    if not token or not phone_id:
        return False

    tel_limpio = "".join(c for c in str(telefono_destinatario) if c.isdigit())
    if len(tel_limpio) == 10:
        tel_limpio = f"57{tel_limpio}"

    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": tel_limpio,
        "type": "text",
        "text": {"preview_url": False, "body": texto_mensaje},
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        logging.getLogger(__name__).warning("Aviso enviando WhatsApp Meta a %s: %s", tel_limpio, e)
        return False

