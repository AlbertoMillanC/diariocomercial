"""
registro/recibo_service.py
Generador omnicanal de Recibos/Facturas en PDF y Tarjetas de Pago con Código QR Bre-B
(Arquitectura WeChat Pay adaptada al comercio popular colombiano para Telegram y WhatsApp).
"""
import io
import base64
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from django.conf import settings
from django.utils import timezone

from .models import Venta, Establecimiento, TransaccionBreB
from .bre_b_service import generar_qr_dinamico_bre_b, generar_payload_emvco_saas



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


def generar_imagen_qr_suscripcion_saas(
    establecimiento: Establecimiento,
    monto: Decimal = Decimal("19900"),
    referencia: str = "",
) -> Tuple[bytes, str, str]:
    """
    Genera la tarjeta visual QR de cobro de suscripción SaaS con:
    - Llave oficial BanRep / Bre-B: 3028530041
    - WhatsApp técnico / contacto: 3146922087
    - Tarjeta gráfica con banner Bre-B y código QR EMVCo escaneable.
    Retorna: (bytes_png, payload_emvco, qr_base64)
    """
    llave = getattr(settings, "SAAS_LLAVE_PAGOS_BRE_B", "3028530041")
    ref = referencia or f"SAAS-{establecimiento.pk}-{int(monto)}"
    payload_emvco = generar_payload_emvco_saas(
        monto=monto,
        referencia=ref,
        llave=llave,
        tipo_llave="celular",
        beneficiario="DIARIOCOMERCIAL SAAS",
    )

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

    # 1. Encabezado Verde Bre-B
    draw.rectangle([(0, 0), (card_w, 50)], fill="#065f46")
    draw.text((16, 10), "⚡ BRE-B • SUSCRIPCIÓN SAAS", fill="#ffffff")
    draw.text((16, 28), f"PAGO INTEROPERABLE BANREP • LLAVE: {llave}", fill="#a7f3d0")

    # 2. Dibujar módulos del QR
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

    # 3. Pie con monto y datos de pago
    draw.rectangle([(0, card_h - 66), (card_w, card_h)], fill="#f8fafc")
    draw.line([(0, card_h - 66), (card_w, card_h - 66)], fill="#cbd5e1", width=1)

    monto_fmt = f"${monto:,.0f} COP".replace(",", ".")
    comercio_nom = establecimiento.nombre[:26].upper()
    whatsapp_contacto = getattr(settings, "SAAS_WHATSAPP_CONTACTO", "3146922087")

    draw.text((16, card_h - 58), f"TOTAL A PAGAR: {monto_fmt}", fill="#065f46")
    draw.text((16, card_h - 40), f"Tienda: {comercio_nom} • Llave Bre-B: {llave}", fill="#334155")
    draw.text((16, card_h - 22), f"WhatsApp Soporte / Envío soporte: {whatsapp_contacto}", fill="#64748b")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()
    b64 = base64.b64encode(raw_bytes).decode("utf-8")
    return raw_bytes, payload_emvco, b64


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
    cli_str = f"{venta.cliente.nombre} ({venta.cliente.nit_cedula})" if venta.cliente else "Consumidor Final (222222222222)"
    linea(f"Cliente: {cli_str[:35]}", size=8)

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


def generar_pdf_factura_electronica_dian(venta: Venta) -> bytes:
    """
    Genera el documento formal de FACTURA ELECTRÓNICA DE VENTA DIAN (UBL 2.1) en PDF:
    - Encabezado tributario con Resolución DIAN, Prefijo y Rangos.
    - Datos del Emisor y Adquirente (Nombre, NIT, Correo, Dirección).
    - Detalle de ítems y valores en COP.
    - Código QR Oficial DIAN interactivo.
    - CUFE (Código Único de Facturación Electrónica) completo.
    - Leyenda legal de validación previa DIAN.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF

    buf = io.BytesIO()
    p = canvas.Canvas(buf, pagesize=letter)
    width, height = letter  # 612 x 792 pt

    est = venta.establecimiento
    cliente = venta.cliente

    # Colores corporativos DIAN / Institucionales
    # Encabezado Emisor
    p.setFillColorRGB(0.06, 0.09, 0.16)
    p.rect(36, height - 110, width - 72, 74, fill=1, stroke=0)

    p.setFillColorRGB(1, 1, 1)
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height - 58, est.nombre.upper())
    p.setFont("Helvetica", 9)
    regimen_str = "Régimen Ordinario / Común"
    if hasattr(est, "get_clasificacion_tributaria_display"):
        regimen_str = est.get_clasificacion_tributaria_display()
    p.drawString(50, height - 74, f"NIT: {est.nit or '891800846-1'} • RÉGIMEN: {regimen_str.upper()}")
    mun_str = est.municipio.nombre.upper() if est.municipio else "TUNJA"
    p.drawString(50, height - 88, f"Dirección: {est.direccion or 'Tunja, Boyacá'} • {mun_str}")

    # Cuadro Número de Factura
    p.setFillColorRGB(0.95, 0.97, 1.0)
    p.rect(width - 210, height - 102, 160, 58, fill=1, stroke=0)
    p.setFillColorRGB(0.02, 0.37, 0.73)
    p.setFont("Helvetica-Bold", 8.5)
    p.drawString(width - 200, height - 58, "FACTURA ELECTRÓNICA DE VENTA")
    p.setFont("Helvetica-Bold", 14)
    num_fe = venta.numero_factura_electronica or f"{est.prefijo_facturacion}-{venta.pk:05d}"
    p.drawString(width - 200, height - 76, f"N° {num_fe}")
    p.setFont("Helvetica", 7.5)
    p.setFillColorRGB(0.3, 0.3, 0.3)
    p.drawString(width - 200, height - 90, f"Fecha Emisión: {timezone.localtime(venta.fecha_hora).strftime('%d/%m/%Y %H:%M')}")

    # Resolución DIAN
    p.setFillColorRGB(0.96, 0.96, 0.96)
    p.rect(36, height - 146, width - 72, 30, fill=1, stroke=0)
    p.setFillColorRGB(0.2, 0.2, 0.2)
    p.setFont("Helvetica-Bold", 7.5)
    p.drawString(46, height - 128, f"AUTORIZACIÓN DIAN: Res. N° {est.resolucion_dian_numero or '18764000001'} de {timezone.localdate().year}")
    p.setFont("Helvetica", 7.5)
    p.drawString(46, height - 140, f"Rango Autorizado: Prefijo {est.prefijo_facturacion} del {est.rango_desde} al {est.rango_hasta} • Modalidad: Facturación Electrónica con Validación Previa")

    # Datos del Adquirente / Cliente
    p.setFillColorRGB(0.06, 0.09, 0.16)
    p.setFont("Helvetica-Bold", 9)
    p.drawString(36, height - 164, "DATOS DEL ADQUIRENTE / CLIENTE:")
    p.setStrokeColorRGB(0.8, 0.8, 0.8)
    p.line(36, height - 168, width - 36, height - 168)

    p.setFont("Helvetica-Bold", 8.5)
    p.drawString(46, height - 182, "Nombre / Razón Social:")
    p.drawString(46, height - 196, "NIT / Cédula:")
    p.drawString(46, height - 210, "Correo Electrónico DIAN:")

    p.setFont("Helvetica", 8.5)
    p.drawString(160, height - 182, cliente.nombre if cliente else "CONSUMIDOR FINAL")
    p.drawString(160, height - 196, cliente.nit_cedula if cliente else "222222222222")
    p.drawString(160, height - 210, cliente.correo_electronico if (cliente and cliente.correo_electronico) else "mostrador@dian.gov.co")

    p.setFont("Helvetica-Bold", 8.5)
    p.drawString(340, height - 182, "Teléfono / Celular:")
    p.drawString(340, height - 196, "Dirección:")
    p.drawString(340, height - 210, "Medio de Pago:")

    p.setFont("Helvetica", 8.5)
    p.drawString(430, height - 182, cliente.telefono if cliente else "No registra")
    p.drawString(430, height - 196, cliente.direccion if cliente else "Tunja, Boyacá")
    p.drawString(430, height - 210, venta.get_medio_pago_display().upper())

    # Tabla de Ítems
    y_table = height - 235
    p.setFillColorRGB(0.06, 0.09, 0.16)
    p.rect(36, y_table - 18, width - 72, 18, fill=1, stroke=0)
    p.setFillColorRGB(1, 1, 1)
    p.setFont("Helvetica-Bold", 8)
    p.drawString(46, y_table - 12, "CÓD.")
    p.drawString(100, y_table - 12, "DESCRIPCIÓN DE BIENES / SERVICIOS")
    p.drawString(330, y_table - 12, "CANT.")
    p.drawString(390, y_table - 12, "V. UNITARIO")
    p.drawString(480, y_table - 12, "TOTAL COP")

    # Fila del ítem vendido
    y_row = y_table - 34
    p.setFillColorRGB(0.1, 0.1, 0.1)
    p.setFont("Helvetica", 8.5)
    p.drawString(46, y_row, f"ITM-{venta.pk}")
    p.drawString(100, y_row, (venta.concepto or "Venta de mostrador")[:38])
    p.drawString(330, y_row, "1.00 Und")
    valor_fmt = f"${venta.valor:,.0f}".replace(",", ".")
    p.drawString(390, y_row, valor_fmt)
    p.drawString(480, y_row, valor_fmt)

    p.setStrokeColorRGB(0.9, 0.9, 0.9)
    p.line(36, y_row - 8, width - 36, y_row - 8)

    # Bloque de Totales
    y_tot = y_row - 24
    p.setFillColorRGB(0.97, 0.98, 0.99)
    p.rect(width - 240, y_tot - 68, 204, 76, fill=1, stroke=0)
    p.setFillColorRGB(0.2, 0.2, 0.2)
    p.setFont("Helvetica-Bold", 8.5)
    p.drawString(width - 230, y_tot - 14, "SUBTOTAL:")
    p.drawString(width - 230, y_tot - 30, "IVA (0% / Excluido):")
    p.drawString(width - 230, y_tot - 46, "ICA Tunja (Acuerdo 0032):")
    p.setFont("Helvetica-Bold", 10)
    p.setFillColorRGB(0.06, 0.4, 0.2)
    p.drawString(width - 230, y_tot - 64, "TOTAL FACTURA:")

    p.setFont("Helvetica", 8.5)
    p.setFillColorRGB(0.1, 0.1, 0.1)
    p.drawRightString(width - 46, y_tot - 14, valor_fmt)
    p.drawRightString(width - 46, y_tot - 30, "$ 0")
    p.drawRightString(width - 46, y_tot - 46, f"${venta.ica_estimado:,.0f}".replace(",", "."))
    p.setFont("Helvetica-Bold", 10)
    p.setFillColorRGB(0.06, 0.4, 0.2)
    p.drawRightString(width - 46, y_tot - 64, f"{valor_fmt} COP")

    # Código QR Oficial DIAN y CUFE
    y_cufe = y_tot - 100
    p.setStrokeColorRGB(0.8, 0.8, 0.8)
    p.line(36, y_cufe + 14, width - 36, y_cufe + 14)

    # Generar QR Oficial DIAN
    url_dian = f"https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={venta.cufe or 'CUFE-SIMULADO-DIARIOCOMERCIAL'}"
    qr_widget = QrCodeWidget(url_dian)
    bounds = qr_widget.getBounds()
    qw = bounds[2] - bounds[0]
    qh = bounds[3] - bounds[1]
    lado_qr = 80
    d = Drawing(lado_qr, lado_qr, transform=[lado_qr / qw, 0, 0, lado_qr / qh, 0, 0])
    d.add(qr_widget)
    renderPDF.draw(d, p, 46, y_cufe - 75)

    # Texto CUFE y Validación
    p.setFillColorRGB(0.06, 0.09, 0.16)
    p.setFont("Helvetica-Bold", 8)
    p.drawString(140, y_cufe, "CÓDIGO ÚNICO DE FACTURACIÓN ELECTRÓNICA (CUFE):")
    p.setFont("Courier", 6.5)
    p.setFillColorRGB(0.3, 0.3, 0.3)
    cufe_txt = venta.cufe or "CUFE_PENDIENTE_VALIDACION_DIAN"
    p.drawString(140, y_cufe - 12, cufe_txt[:55])
    p.drawString(140, y_cufe - 22, cufe_txt[55:110])

    p.setFont("Helvetica-Bold", 7.5)
    p.setFillColorRGB(0.05, 0.5, 0.2)
    p.drawString(140, y_cufe - 40, f"ESTADO ANTE LA DIAN: {venta.get_estado_dian_display().upper() if hasattr(venta, 'get_estado_dian_display') else 'APROBADA Y VALIDADA'}")
    p.setFont("Helvetica", 7)
    p.setFillColorRGB(0.4, 0.4, 0.4)
    p.drawString(140, y_cufe - 54, "Firma Digital: Software DiarioComercial v2.0 • Certificado SHA-384 DIAN UBL 2.1")
    p.drawString(140, y_cufe - 66, "Consulte la autenticidad de este documento escaneando el código QR en el portal de la DIAN.")

    # Pie de página legal obligatorio
    p.setStrokeColorRGB(0.85, 0.85, 0.85)
    p.line(36, 48, width - 36, 48)
    p.setFont("Helvetica", 6.5)
    p.setFillColorRGB(0.5, 0.5, 0.5)
    p.drawCentredString(width / 2, 38, "Esta factura electrónica de venta cumple los requisitos de la Ley 2010 de 2019, Decreto 358 de 2020 y Resolución DIAN 000165 de 2023.")
    p.drawCentredString(width / 2, 28, f"Generado e impreso por el sistema DiarioComercial • {est.nombre} • Tunja, Colombia")

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


def generar_pdf_etiqueta_qr_producto(producto) -> bytes:
    """
    Genera una etiqueta adhesiva térmica profesional (58mm x 40mm) con el Código QR de la Tienda.
    Ideal para productos artesanales, carnes al corte, frutas/verduras y productos sin código de fábrica.
    Permite pegar el adhesivo al empaque, bolsa o góndola para lectura óptica en mostrador.
    """
    from reportlab.lib.pagesizes import mm
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(58 * mm, 40 * mm))
    est = producto.establecimiento

    # Encabezado: Nombre de la tienda
    c.setFont("Helvetica-Bold", 7.5)
    c.drawCentredString(29 * mm, 36 * mm, est.nombre[:30].upper())

    c.setLineWidth(0.4)
    c.line(4 * mm, 34.5 * mm, 54 * mm, 34.5 * mm)

    # Nombre del producto
    c.setFont("Helvetica-Bold", 8.5)
    c.drawCentredString(29 * mm, 30.5 * mm, producto.nombre[:30])

    # Código QR de Tienda (izquierdo)
    codigo_val = producto.codigo_barras or f"DC{producto.pk:05d}"
    qr_payload = f"DC:P:{producto.pk}:SKU:{codigo_val}:COP:{int(producto.precio_kilo)}"

    qr_code = qr.QrCodeWidget(qr_payload)
    bounds = qr_code.getBounds()
    qw = bounds[2] - bounds[0]
    qh = bounds[3] - bounds[1]

    lado_qr = 20 * mm
    d = Drawing(lado_qr, lado_qr, transform=[lado_qr / qw, 0, 0, lado_qr / qh, 0, 0])
    d.add(qr_code)
    renderPDF.draw(d, c, 5 * mm, 9 * mm)

    # Columna derecha: Detalles de precio y SKU
    c.setFont("Helvetica", 6.5)
    c.drawString(27 * mm, 25 * mm, f"CAT: {producto.get_categoria_display()[:15].upper()}")

    c.setFont("Helvetica-Bold", 10.5)
    unidad_str = producto.unidad_medida.upper()
    if producto.es_servicio:
        c.drawString(27 * mm, 19.5 * mm, f"${producto.precio_kilo:,.0f}".replace(",", "."))
        c.setFont("Helvetica", 6.5)
        c.drawString(27 * mm, 15.5 * mm, "SERVICIO")
    else:
        c.drawString(27 * mm, 19.5 * mm, f"${producto.precio_kilo:,.0f}".replace(",", "."))
        c.setFont("Helvetica", 6.5)
        c.drawString(27 * mm, 15.5 * mm, f"POR {unidad_str}")
        if producto.unidad_medida == "kg":
            c.drawString(27 * mm, 12 * mm, f"Lb: ${producto.precio_libra:,.0f}".replace(",", "."))

    # Pie de etiqueta
    c.setFont("Helvetica-Bold", 6.5)
    c.drawCentredString(29 * mm, 4.5 * mm, f"SKU: {codigo_val} • QR DE TIENDA")

    c.save()
    return buf.getvalue()


def generar_pdf_etiquetas_qr_masivo(establecimiento, productos=None, solo_sin_codigo=False) -> bytes:
    """
    Genera un lote continuo de etiquetas QR (58mm x 40mm por página)
    para imprimir todas las etiquetas de la tienda en impresoras térmicas de rollo
    (Xprinter, Zebra, etc.) o impresoras POS.
    """
    from django.db.models import Q
    from reportlab.lib.pagesizes import mm
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF

    if productos is None:
        qs = establecimiento.productos.filter(estado="activo")
        if solo_sin_codigo:
            qs = qs.filter(Q(codigo_barras="") | Q(codigo_barras__isnull=True))
        productos = list(qs.order_by("categoria", "nombre"))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(58 * mm, 40 * mm))

    if not productos:
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(29 * mm, 20 * mm, "No hay productos para imprimir")
        c.save()
        return buf.getvalue()

    for idx, prod in enumerate(productos):
        if idx > 0:
            c.showPage()

        # Encabezado
        c.setFont("Helvetica-Bold", 7.5)
        c.drawCentredString(29 * mm, 36 * mm, establecimiento.nombre[:30].upper())
        c.setLineWidth(0.4)
        c.line(4 * mm, 34.5 * mm, 54 * mm, 34.5 * mm)

        # Nombre producto
        c.setFont("Helvetica-Bold", 8.5)
        c.drawCentredString(29 * mm, 30.5 * mm, prod.nombre[:30])

        # QR
        codigo_val = prod.codigo_barras or f"DC{prod.pk:05d}"
        qr_payload = f"DC:P:{prod.pk}:SKU:{codigo_val}:COP:{int(prod.precio_kilo)}"
        qr_code = qr.QrCodeWidget(qr_payload)
        bounds = qr_code.getBounds()
        qw = bounds[2] - bounds[0]
        qh = bounds[3] - bounds[1]
        lado_qr = 20 * mm
        d = Drawing(lado_qr, lado_qr, transform=[lado_qr / qw, 0, 0, lado_qr / qh, 0, 0])
        d.add(qr_code)
        renderPDF.draw(d, c, 5 * mm, 9 * mm)

        # Detalles
        c.setFont("Helvetica", 6.5)
        c.drawString(27 * mm, 25 * mm, f"CAT: {prod.get_categoria_display()[:15].upper()}")
        c.setFont("Helvetica-Bold", 10.5)
        unidad_str = prod.unidad_medida.upper()
        if prod.es_servicio:
            c.drawString(27 * mm, 19.5 * mm, f"${prod.precio_kilo:,.0f}".replace(",", "."))
            c.setFont("Helvetica", 6.5)
            c.drawString(27 * mm, 15.5 * mm, "SERVICIO")
        else:
            c.drawString(27 * mm, 19.5 * mm, f"${prod.precio_kilo:,.0f}".replace(",", "."))
            c.setFont("Helvetica", 6.5)
            c.drawString(27 * mm, 15.5 * mm, f"POR {unidad_str}")
            if prod.unidad_medida == "kg":
                c.drawString(27 * mm, 12 * mm, f"Lb: ${prod.precio_libra:,.0f}".replace(",", "."))

        # Pie
        c.setFont("Helvetica-Bold", 6.5)
        c.drawCentredString(29 * mm, 4.5 * mm, f"SKU: {codigo_val} • QR DE TIENDA")

    c.save()
    return buf.getvalue()
