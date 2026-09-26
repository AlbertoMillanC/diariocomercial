"""
registro/bre_b_service.py
Motor de Pagos Interoperables Bre-B (Banco de la República de Colombia)
- Generación de Códigos QR Dinámicos (Estándar EMVCo MPM v1.0 + CRC-16/CCITT-FALSE).
- Webhook de confirmación en tiempo real (<2s) con verificación criptográfica HMAC-SHA256.
- Integración atómica con ventas, descuento de stock e imputación contable ICA.
- Generador de confirmación visual (Token de 3 caracteres) y auditiva (Soundbox TTS).
"""
import io
import base64
import hashlib
import hmac
import json
import secrets
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Establecimiento, Venta, Producto, TransaccionBreB
from .inventario_service import procesar_salida_inventario, normalizar_texto


def format_tlv(tag: str, value: str) -> str:
    """Empaqueta un par Tag-Length-Value según la especificación EMVCo."""
    length = f"{len(value):02d}"
    return f"{tag}{length}{value}"


def calcular_crc16_ccitt(payload_str: str) -> str:
    """
    Calcula CRC-16/CCITT-FALSE (polinomio 0x1021, init 0xFFFF).
    Estándar obligatorio para el Tag 63 de EMVCo / Bre-B.
    """
    crc = 0xFFFF
    data = payload_str.encode("utf-8")
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def generar_micro_token() -> str:
    """Genera un token efímero de 3 caracteres alfanuméricos legibles (ej: #7A2)."""
    caracteres = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "#" + "".join(secrets.choice(caracteres) for _ in range(3))


def generar_qr_dinamico_bre_b(
    establecimiento: Establecimiento,
    monto: Decimal,
    comando_original: str = "",
    mcc: str = "5411",
    ciudad: str = "TUNJA",
) -> Tuple[TransaccionBreB, str]:
    """
    Construye la transacción y el payload EMVCo Bre-B con monto embebido y referencia única.
    Retorna: (instancia_transaccion, payload_emvco_completo)
    """
    referencia = f"DC-{timezone.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4).upper()}"
    token_corto = generar_micro_token()
    llave = establecimiento.llave_bre_b or (establecimiento.nit or "3100000000")
    tipo_llave = establecimiento.tipo_llave_bre_b or "celular"

    # Sub-tags Tag 26 (Merchant Account Information Bre-B)
    subtag_guid = format_tlv("00", "co.gov.banrep.bre-b")
    subtag_key = format_tlv("01", f"{tipo_llave}:{llave}")
    tag_26 = format_tlv("26", f"{subtag_guid}{subtag_key}")

    # Sub-tags Tag 62 (Additional Data)
    subtag_ref = format_tlv("05", referencia[:25])
    subtag_terminal = format_tlv("07", "DC-BREB-POS")
    tag_62 = format_tlv("62", f"{subtag_ref}{subtag_terminal}")

    monto_str = f"{monto:.2f}"
    nombre_limpio = "".join(c for c in normalizar_texto(establecimiento.nombre) if c.isalnum() or c == " ")[:25].upper()
    ciudad_limpia = ciudad[:15].upper()

    trama_base = (
        format_tlv("00", "01") +                # Versión EMVCo
        format_tlv("01", "12") +                # 12 = Dinámico (monto fijo)
        tag_26 +                                # Bre-B BanRep
        format_tlv("52", mcc) +                 # MCC
        format_tlv("53", "170") +               # ISO 4217 COP
        format_tlv("54", monto_str) +           # Monto
        format_tlv("58", "CO") +                # Colombia
        format_tlv("59", nombre_limpio) +       # Comercio
        format_tlv("60", ciudad_limpia) +       # Ciudad
        tag_62 +                                # Referencia
        "6304"                                  # Checksum header
    )

    crc = calcular_crc16_ccitt(trama_base)
    payload_emvco = f"{trama_base}{crc}"

    # Registrar en base de datos
    tx = TransaccionBreB.objects.create(
        establecimiento=establecimiento,
        referencia_unica=referencia,
        token_visual_corto=token_corto,
        monto=monto,
        llave_utilizada=llave,
        payload_emvco=payload_emvco,
        comando_original=comando_original,
        estado="iniciada",
    )

    return tx, payload_emvco


def verificar_firma_webhook(cuerpo_bytes: bytes, firma_recibida: str, secreto: str) -> bool:
    """Valida la firma HMAC-SHA256 del webhook entrante del adquirente/switch."""
    if not firma_recibida or not secreto:
        return False
    firma_calculada = hmac.new(secreto.encode("utf-8"), cuerpo_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(firma_recibida.lower(), firma_calculada.lower())


def procesar_confirmacion_bre_b(
    referencia_unica: str,
    monto_acreditado: Decimal,
    banrep_transaction_id: str = "",
    banco_origen: str = "Bancolombia / Nequi",
    nombre_pagador: str = "Cliente",
) -> Tuple[bool, str, Optional[Venta], Optional[TransaccionBreB]]:
    """
    Procesamiento atómico e idempotente de la confirmación de pago Bre-B.
    Retorna: (exito, mensaje_resultado, venta, transaccion)
    """
    with transaction.atomic():
        tx = TransaccionBreB.objects.select_for_update().filter(referencia_unica=referencia_unica).first()
        if not tx:
            return False, f"Transacción {referencia_unica} no encontrada", None, None

        if tx.estado == "aprobada":
            # Idempotencia perfecta: ya fue procesada
            return True, "Transacción ya procesada previamente (Idempotente)", tx.venta, tx

        if tx.monto != monto_acreditado:
            tx.estado = "rechazada"
            tx.save()
            return False, f"Discrepancia en monto: esperado ${tx.monto}, recibido ${monto_acreditado}", None, tx

        establecimiento = tx.establecimiento
        # Usuario del establecimiento o primer usuario vinculado
        usuario = establecimiento.perfil_set.first().user if establecimiento.perfil_set.exists() else None

        # Procesar descuento de inventario si vino un comando
        concepto_venta = f"Pago Bre-B {tx.token_visual_corto}"
        if tx.comando_original:
            prod, kg_desc, _, _, _ = procesar_salida_inventario(
                establecimiento, tx.comando_original, valor_ingresado=monto_acreditado
            )
            if prod:
                concepto_venta = f"{prod.nombre} (Bre-B)"

        # Crear la venta en libros
        actividad = establecimiento.actividades.first()
        venta = Venta.objects.create(
            establecimiento=establecimiento,
            usuario=usuario,
            actividad=actividad,
            fecha=timezone.localdate(),
            fecha_hora=timezone.now(),
            valor=monto_acreditado,
            concepto=concepto_venta,
            observacion=f"Acreditado vía Bre-B ({banco_origen}) - CUS: {banrep_transaction_id} - Pagador: {nombre_pagador}",
            medio_pago="bre_b",
            estado="vigente",
        )

        # Actualizar transacción Bre-B
        tx.estado = "aprobada"
        tx.id_transaccion_banrep = banrep_transaction_id
        tx.banco_origen = banco_origen
        tx.venta = venta
        tx.fecha_confirmacion = timezone.now()
        tx.save()

    monto_fmt = f"{int(monto_acreditado):,}".replace(",", ".")
    texto_voz = f"¡Bre-B recibido: {monto_fmt} pesos de {nombre_pagador}!"
    return True, texto_voz, venta, tx


def generar_payload_emvco_saas(
    monto: Decimal,
    referencia: str,
    llave: str = "3028530041",
    tipo_llave: str = "celular",
    beneficiario: str = "DIARIOCOMERCIAL SAAS",
    ciudad: str = "TUNJA",
) -> str:
    """Genera el payload EMVCo MPM oficial de cobro Bre-B para suscripciones SaaS."""
    subtag_guid = format_tlv("00", "co.gov.banrep.bre-b")
    subtag_key = format_tlv("01", f"{tipo_llave}:{llave}")
    tag_26 = format_tlv("26", f"{subtag_guid}{subtag_key}")

    subtag_ref = format_tlv("05", referencia[:25])
    subtag_terminal = format_tlv("07", "DC-SAAS-BILLING")
    tag_62 = format_tlv("62", f"{subtag_ref}{subtag_terminal}")

    monto_str = f"{monto:.2f}"
    nombre_limpio = "".join(c for c in normalizar_texto(beneficiario) if c.isalnum() or c == " ")[:25].upper()
    ciudad_limpia = ciudad[:15].upper()

    trama_base = (
        format_tlv("00", "01") +                # Versión EMVCo
        format_tlv("01", "12") +                # 12 = Dinámico (monto fijo)
        tag_26 +                                # Bre-B BanRep
        format_tlv("52", "7372") +              # MCC (Computer Software Services)
        format_tlv("53", "170") +               # ISO 4217 COP
        format_tlv("54", monto_str) +           # Monto de la mensualidad
        format_tlv("58", "CO") +                # País Colombia
        format_tlv("59", nombre_limpio) +       # DIARIOCOMERCIAL SAAS
        format_tlv("60", ciudad_limpia) +       # TUNJA
        tag_62 +                                # Datos adicionales
        "6304"                                  # Tag 63 CRC
    )
    crc = calcular_crc16_ccitt(trama_base)
    return f"{trama_base[:-4]}{format_tlv('63', crc)}"

