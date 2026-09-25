"""
registro/recibo_service.py
Generador omnicanal de Recibos/Facturas en PDF y Tarjetas de Pago con Código QR Bre-B
(Arquitectura WeChat Pay adaptada al comercio popular colombiano para Telegram y WhatsApp).
"""
import io
from decimal import Decimal
from typing import Dict, Any, Optional

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from django.utils import timezone

from .models import Venta, Establecimiento, TransaccionBreB
from .bre_b_service import generar_qr_dinamico_bre_b


def generar_imagen_qr_bre_b(
    monto: Decimal,
    establecimiento: Establecimiento,
    referencia: str = "",
    payload_emvco: str = "",
) -> bytes:
    """
    Genera una tarjeta visual de pago de alto impacto (estilo WeChat Pay) con:
    - Banner institucional Bre-B (Banco de la República).
    - Código QR interoperable EMVCo con monto exacto embebido.
    - Monto a pagar en pesos enteros sin decimales.
    - Nombre del comercio y referencia única.
    """
    if not payload_emvco:
        _, payload_emvco = generar_qr_dinamico_bre_b(
            establecimiento=establecimiento,
            monto=monto,
            comando_original=f"Cobro Bre-B {monto}",
        )

    # 1. Renderizar la matriz del código QR usando ReportLab
    widget = QrCodeWidget(payload_emvco)
    widget.qr.make()
    mod_count = widget.qr.getModuleCount()

    box = 8
    border = 3
    qr_size = (mod_count + border * 2) * box

    card_w = max(qr_size + 40, 360)
    card_h = qr_size + 150

    img = Image.new("RGB", (card_w, card_h), "#ffffff")
    draw = ImageDraw.Draw(img)

    # 2. Encabezado Verde Bre-B
    draw.rectangle([(0, 0), (card_w, 50)], fill="#065f46")
    draw.text((16, 10), "⚡ BRE-B • BANCO DE LA REPÚBLICA", fill="#ffffff")
    draw.text((16, 28), "PAGO INTEROPERABLE EN 2 SEGUNDOS", fill="#a7f3d0")

    # 3. Dibujar módulos del QR en el centro
    x_offset = (card_w - qr_size) // 2
    y_offset = 60
    for r in range(mod_count):
        for c in range(mod_count):
            if widget.qr.isDark(r, c):
                x0 = x_offset + (c + border) * box
                y0 = y_offset + (r + border) * box
                x1 = x0 + box - 1
                y1 = y0 + box - 1
                draw.rectangle([(x0, y0), (x1, y1)], fill="#0f172a")

    # 4. Pie de Tarjeta con Monto y Datos del Comercio
    draw.rectangle([(0, card_h - 66), (card_w, card_h)], fill="#f8fafc")
    draw.line([(0, card_h - 66), (card_w, card_h - 66)], fill="#cbd5e1", width=1)

    monto_fmt = f"${monto:,.0f} COP".replace(",", ".")
    comercio_nom = establecimiento.nombre[:30].upper()

    draw.text((16, card_h - 58), f"TOTAL A PAGAR: {monto_fmt}", fill="#065f46")
    draw.text((16, card_h - 40), f"Comercio: {comercio_nom} • Ref: {referencia[:16] or 'BRE-B'}", fill="#334155")
    draw.text((16, card_h - 22), "Escanee con Nequi, Daviplata o cualquier banco", fill="#64748b")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generar_pdf_recibo_venta(venta: Venta) -> bytes:
    """
    Genera un comprobante oficial de venta / factura de mostrador en formato PDF
    estandarizado para rollo térmico o consulta en dispositivos móviles (Telegram / WhatsApp).
    """
    buf = io.BytesIO()
    # Ancho 226 pt (~80mm) x Alto 440 pt
    p = canvas.Canvas(buf, pagesize=(226, 460))
    est = venta.establecimiento

    y = 440
    def linea(texto: str, size: int = 8, bold: bool = False, centro: bool = False, sep: int = 12):
        nonlocal y
        y -= sep
        font_name = "Helvetica-Bold" if bold else "Helvetica"
        p.setFont(font_name, size)
        if centro:
            p.drawCentredString(113, y, str(texto)[:45])
        else:
            p.drawString(14, y, str(texto)[:45])

    def divisor():
        nonlocal y
        y -= 8
        p.setFont("Helvetica", 7)
        p.drawString(14, y, "------------------------------------------------------------")

    # Encabezado Comercial
    linea("DIARIOCOMERCIAL", size=11, bold=True, centro=True, sep=14)
    linea(est.nombre.upper(), size=10, bold=True, centro=True)
    nit_str = f"NIT: {est.nit or '891800846-1'}"
    if est.municipio:
        nit_str += f" - {est.municipio.nombre.upper()}"
    linea(nit_str, size=8, centro=True)
    if est.direccion:
        linea(est.direccion[:40], size=7, centro=True)

    divisor()
    linea("COMPROBANTE DE PAGO / FACTURA", size=9, bold=True, centro=True)
    linea(f"TICKET NÚMERO: #{venta.pk:05d}", size=9, bold=True, centro=True)
    
    hora_str = timezone.localtime(venta.fecha_hora).strftime("%d/%m/%Y  %I:%M %p")
    linea(f"Fecha: {hora_str}", size=8)
    cajero = venta.usuario.get_full_name() or venta.usuario.username if venta.usuario else "Cajero"
    linea(f"Atendido por: {cajero}", size=8)

    divisor()
    linea("DETALLE DE LA OPERACIÓN", size=8, bold=True)
    linea(f"Concepto: {venta.concepto[:35]}", size=8)
    linea(f"Medio de Pago: {venta.get_medio_pago_display() or venta.medio_pago.upper()}", size=8, bold=True)

    divisor()
    valor_fmt = f"${venta.valor:,.0f} COP".replace(",", ".")
    ica_fmt = f"${venta.ica_estimado:,.0f} COP".replace(",", ".")

    linea(f"Subtotal:           {valor_fmt}", size=8)
    linea(f"ICA Tunja (Acuerdo 0032): {ica_fmt}", size=7)
    
    divisor()
    linea(f"TOTAL PAGADO: {valor_fmt}", size=11, bold=True)
    divisor()

    linea("ESTADO: PAGADO Y CUADRADO", size=8, bold=True, centro=True)
    linea("¡Muchas gracias por su compra!", size=8, centro=True)
    linea(f"Visítenos de nuevo en {est.nombre}", size=7, centro=True)
    divisor()
    linea("Plataforma DiarioComercial v2.0", size=6, centro=True)

    p.save()
    return buf.getvalue()


def preparar_paquete_omnicanal_venta(
    venta: Venta,
    celular_cliente: str = "",
) -> Dict[str, Any]:
    """
    Empaqueta los elementos para distribución omnicanal:
    1. Confirmación de texto.
    2. Tarjeta con Código QR Bre-B en PNG.
    3. Recibo oficial en PDF.
    4. Estructura de metadatos lista para el webhook de WhatsApp Cloud API.
    """
    est = venta.establecimiento
    qr_png = generar_imagen_qr_bre_b(
        monto=venta.valor,
        establecimiento=est,
        referencia=f"TICK-{venta.pk}",
    )
    pdf_bytes = generar_pdf_recibo_venta(venta)

    valor_fmt = f"${venta.valor:,.0f} COP".replace(",", ".")
    caption_qr = (
        f"⚡ *Cobro Rápido Bre-B (WeChat Pay de Colombia)*\n"
        f"💰 *Monto a Pagar:* {valor_fmt}\n"
        f"🏪 *Comercio:* {est.nombre}\n"
        f"📲 El cliente puede escanear este QR desde *Nequi, Daviplata, Bancolombia* o cualquier app bancaria."
    )
    caption_pdf = (
        f"🧾 *Recibo de Pago / Factura Oficial*\n"
        f"Ticket #{venta.pk:05d} • {valor_fmt}\n"
        f"Establecimiento: {est.nombre}"
    )

    # Estructura preparada para WhatsApp Cloud API (Graph API v20.0)
    whatsapp_fields = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": celular_cliente or getattr(est, "llave_bre_b", ""),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "image", "image_bytes_len": len(qr_png)},
            "body": {"text": f"Hola! Aquí tienes tu recibo y QR de pago por {valor_fmt} de {est.nombre}."},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": f"pagado_{venta.pk}", "title": "Confirmar Pago"}},
                    {"type": "reply", "reply": {"id": f"factura_{venta.pk}", "title": "Descargar PDF"}}
                ]
            }
        },
        "document_attachment": {
            "filename": f"recibo_{est.nombre}_{venta.pk}.pdf",
            "bytes_len": len(pdf_bytes),
            "mime_type": "application/pdf",
        }
    }

    return {
        "venta": venta,
        "valor_fmt": valor_fmt,
        "qr_bytes": qr_png,
        "qr_caption": caption_qr,
        "pdf_bytes": pdf_bytes,
        "pdf_filename": f"recibo_ticket_{venta.pk}.pdf",
        "pdf_caption": caption_pdf,
        "whatsapp_fields": whatsapp_fields,
    }


def generar_tarjeta_qr_producto(producto) -> bytes:
    """
    Genera una tarjeta visual de cobro instantáneo Bre-B para un producto o servicio específico.
    Incluye código QR interoperable, precio por unidad/kilo, nombre y categoría.
    """
    est = producto.establecimiento
    monto = producto.precio_kilo
    referencia = f"PROD-{producto.pk}-{producto.codigo_barras or 'ITEM'}"
    return generar_imagen_qr_bre_b(
        monto=monto,
        establecimiento=est,
        referencia=referencia,
    )


def generar_pdf_etiqueta_barras(producto) -> bytes:
    """
    Genera una etiqueta de código de barras profesional para rollo térmico estándar (58mm x 40mm)
    usada por impresoras POS o etiquetadoras de mostrador (Zebra, Xprinter, etc.).
    """
    from reportlab.lib.pagesizes import mm
    from reportlab.graphics.barcode import code128

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(58 * mm, 40 * mm))
    est = producto.establecimiento

    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(29 * mm, 35 * mm, est.nombre[:30].upper())

    c.setFont("Helvetica", 7.5)
    c.drawCentredString(29 * mm, 31.5 * mm, producto.nombre[:32])

    c.setFont("Helvetica-Bold", 9.5)
    unidad_str = producto.unidad_medida.upper()
    if producto.es_servicio:
        precio_str = f"SERVICIO: ${producto.precio_kilo:,.0f} COP".replace(",", ".")
    else:
        precio_str = f"${producto.precio_kilo:,.0f} COP / {unidad_str}".replace(",", ".")
    c.drawCentredString(29 * mm, 27 * mm, precio_str)

    # Código de barras (Code128)
    codigo_val = producto.codigo_barras or f"DC{producto.pk:06d}"
    try:
        bc = code128.Code128(codigo_val, barHeight=11 * mm, barWidth=0.35 * mm, humanReadable=True)
        bc.drawOn(c, 6 * mm, 6 * mm)
    except Exception:
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(29 * mm, 12 * mm, f"* {codigo_val} *")

    c.save()
    return buf.getvalue()
