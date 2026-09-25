"""
registro/print_service.py
Generador de comandos binarios universales ESC/POS para impresoras térmicas (58mm / 80mm).
Diseñado para operar con el agente local 'diario-print-daemon' sin depender de drivers
de Windows ni fuentes nativas chinas, garantizando caracteres 100% legibles y corte de papel.
"""
from decimal import Decimal
from typing import Optional, List, Dict, Any
from .models import Venta
from .inventario_service import normalizar_texto


# Códigos de control estándar ESC/POS
ESC = b"\x1b"
GS = b"\x1d"

CMD_INIT = ESC + b"@"                    # Inicializar impresora
CMD_ALIGN_LEFT = ESC + b"a\x00"          # Alinear a la izquierda
CMD_ALIGN_CENTER = ESC + b"a\x01"        # Alinear al centro
CMD_ALIGN_RIGHT = ESC + b"a\x02"         # Alinear a la derecha
CMD_BOLD_ON = ESC + b"E\x01"             # Negrita activada
CMD_BOLD_OFF = ESC + b"E\x00"            # Negrita desactivada
CMD_DOUBLE_HEIGHT = ESC + b"!\x10"       # Doble altura
CMD_NORMAL_SIZE = ESC + b"!\x00"         # Tamaño normal
CMD_CUT_PAPER = GS + b"V\x42\x00"        # Corte total con avance (Feed & Cut)


def limpiar_ascii(texto: str) -> str:
    """Convierte caracteres acentuados en ASCII limpio para evitar ideogramas en impresoras genéricas."""
    reemplazos = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U",
        "ñ": "n", "Ñ": "N", "¿": "", "¡": "", "—": "-",
    }
    t = texto or ""
    for orig, dest in reemplazos.items():
        t = t.replace(orig, dest)
    return t


def formatear_linea_doble_columna(izq: str, der: str, ancho_caracteres: int = 32) -> str:
    """Formatea una línea justificando texto a la izquierda y valor a la derecha (ej: 'Carne molida     $40.000')."""
    izq_limpio = limpiar_ascii(izq)
    der_limpio = limpiar_ascii(der)
    espacio_disponible = ancho_caracteres - len(der_limpio)
    if len(izq_limpio) > espacio_disponible:
        izq_limpio = izq_limpio[: espacio_disponible - 1]
    relleno = " " * (ancho_caracteres - len(izq_limpio) - len(der_limpio))
    return f"{izq_limpio}{relleno}{der_limpio}\n"


def generar_bytes_escpos_recibo(
    venta: Venta,
    ancho_papel_mm: int = 58,
    url_recibo_digital: str = "",
) -> bytes:
    """
    Genera el flujo binario crudo ESC/POS para expulsar el recibo en la impresora térmica.
    Ancho: 32 caracteres para 58mm, 42 o 48 caracteres para 80mm.
    """
    ancho_cols = 32 if ancho_papel_mm == 58 else 42
    est = venta.establecimiento
    separador = ("=" * ancho_cols) + "\n"
    separador_fino = ("-" * ancho_cols) + "\n"

    buffer = bytearray()
    buffer.extend(CMD_INIT)

    # 1. ENCABEZADO
    buffer.extend(CMD_ALIGN_CENTER)
    buffer.extend(CMD_BOLD_ON)
    buffer.extend(CMD_DOUBLE_HEIGHT)
    buffer.extend(limpiar_ascii(est.nombre).upper().encode("ascii", "ignore") + b"\n")
    buffer.extend(CMD_NORMAL_SIZE)
    buffer.extend(CMD_BOLD_OFF)

    if est.nit:
        buffer.extend(f"NIT: {est.nit}\n".encode("ascii", "ignore"))
    if est.direccion:
        buffer.extend(limpiar_ascii(est.direccion).encode("ascii", "ignore") + b"\n")
    if est.municipio:
        buffer.extend(f"{limpiar_ascii(est.municipio.nombre)} - Colombia\n".encode("ascii", "ignore"))

    buffer.extend(separador.encode("ascii"))

    # 2. METADATOS DE TICKET
    buffer.extend(CMD_ALIGN_LEFT)
    buffer.extend(f"TICKET VENTA #{venta.pk}\n".encode("ascii"))
    fecha_str = venta.fecha.strftime("%d/%m/%Y")
    buffer.extend(f"Fecha: {fecha_str}\n".encode("ascii"))
    buffer.extend(separador_fino.encode("ascii"))

    # 3. DETALLE DE PRODUCTOS / CONCEPTO
    concepto = venta.concepto or "Venta de mostrador"
    valor_fmt = f"${venta.valor:,.0f}".replace(",", ".")
    buffer.extend(formatear_linea_doble_columna(concepto, valor_fmt, ancho_cols).encode("ascii"))

    buffer.extend(separador_fino.encode("ascii"))

    # 4. TOTALES & MEDIO DE PAGO
    buffer.extend(CMD_BOLD_ON)
    buffer.extend(formatear_linea_doble_columna("TOTAL PAGADO:", valor_fmt, ancho_cols).encode("ascii"))
    buffer.extend(CMD_BOLD_OFF)

    medio_str = dict(Venta.MEDIOS_PAGO).get(venta.medio_pago, venta.medio_pago).upper()
    buffer.extend(f"Medio de Pago: {medio_str}\n".encode("ascii"))

    # 5. PIE DE PÁGINA & RECIBO DIGITAL
    buffer.extend(separador.encode("ascii"))
    buffer.extend(CMD_ALIGN_CENTER)
    buffer.extend(b"Gracias por su compra!\n")
    buffer.extend(b"Conserve este comprobante\n")
    if url_recibo_digital:
        buffer.extend(b"\nConsulte su garantia en:\n")
        buffer.extend(url_recibo_digital.encode("ascii") + b"\n")

    # Avance de papel para no cortar sobre el texto
    buffer.extend(b"\n\n\n")
    buffer.extend(CMD_CUT_PAPER)

    return bytes(buffer)
