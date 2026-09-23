import re
from decimal import Decimal
from typing import Optional, Tuple
from .models import Producto, Auditoria


def normalizar_texto(texto: str) -> str:
    """Normaliza texto removiendo acentos comunes y convirtiendo a minúsculas."""
    t = (texto or "").lower().strip()
    replacements = (
        ("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
        ("ü", "u"), ("ñ", "n")
    )
    for a, b in replacements:
        t = t.replace(a, b)
    return t


def parsear_peso(texto: str) -> Optional[Decimal]:
    """
    Extrae el peso en KILOS de una frase en español.
    Ejemplos:
      '1 libra' -> 0.5
      'una libra' -> 0.5
      '2 libras' -> 1.0
      'media libra' -> 0.25
      '1/2 libra' -> 0.25
      'cuarto de libra' -> 0.125
      '1/4 libra' -> 0.125
      '3/4 libra' -> 0.375
      '1 kilo' -> 1.0
      'un kilo' -> 1.0
      '2.5 kg' -> 2.5
      '500g' -> 0.5
      '500 gramos' -> 0.5
    """
    t = normalizar_texto(texto)

    # Fracciones comunes de libra
    if "media libra" in t or "1/2 libra" in t or "1/2 lb" in t or "medio kilo" in t or "1/2 kilo" in t or "1/2 kg" in t:
        if "kilo" in t or "kg" in t:
            return Decimal("0.5")
        return Decimal("0.25")
    if "cuarto de libra" in t or "1/4 libra" in t or "1/4 lb" in t:
        return Decimal("0.125")
    if "3/4 libra" in t or "3/4 lb" in t:
        return Decimal("0.375")

    # Palabras de números (un/una/dos/tres/cuatro/cinco)
    palabras_num = {
        "un": Decimal("1"),
        "una": Decimal("1"),
        "dos": Decimal("2"),
        "tres": Decimal("3"),
        "cuatro": Decimal("4"),
        "cinco": Decimal("5"),
        "medio": Decimal("0.5"),
    }
    for palabra, num in palabras_num.items():
        m_palabra = re.search(rf"\b{palabra}\s*(libras?|lb|kilos?|kg|gramos?|g)\b", t)
        if m_palabra:
            unidad = m_palabra.group(1)
            if "k" in unidad:
                return num
            elif "l" in unidad:
                return (num * Decimal("0.5")).quantize(Decimal("0.001"))
            elif "g" in unidad:
                return (num / Decimal("1000")).quantize(Decimal("0.001"))

    # Números numéricos con unidad: ej 1.5 kg, 2 libras, 500 g
    m = re.search(r"(\d+(?:[\.,]\d+)?)\s*(kilos?|kg|libras?|lb|gramos?|g)\b", t)
    if m:
        num_str = m.group(1).replace(",", ".")
        try:
            cant = Decimal(num_str)
            unidad = m.group(2)
            if "k" in unidad:
                return cant.quantize(Decimal("0.01"))
            elif "l" in unidad:
                return (cant * Decimal("0.5")).quantize(Decimal("0.01"))
            elif "g" in unidad:
                return (cant / Decimal("1000")).quantize(Decimal("0.001"))
        except Exception:
            pass

    return None


def buscar_producto_en_texto(establecimiento, texto: str) -> Optional[Producto]:
    """Busca el producto de inventario más afín al texto ingresado."""
    t = normalizar_texto(texto)
    productos = list(
        Producto.objects.filter(establecimiento=establecimiento, estado="activo")
    )
    # Ordenar por longitud de nombre descendente para priorizar "carne molida" sobre "carne"
    productos.sort(key=lambda p: len(p.nombre), reverse=True)

    # 1. Búsqueda exacta del nombre en el texto
    for p in productos:
        if normalizar_texto(p.nombre) in t:
            return p

    # 2. Búsqueda por palabras clave significativas
    # Mapeo de sinónimos y palabras comunes de carnicería en Boyacá
    sinonimos = {
        "molida": "molida",
        "lomo": "lomo fino",
        "lomito": "lomo fino",
        "pechuga": "pechuga",
        "pollo": "pechuga",
        "costilla": "costilla",
        "costillitas": "costilla",
        "sobrebarriga": "sobrebarriga",
        "falda": "sobrebarriga",
        "tocino": "tocino",
        "chicharron": "tocino",
        "chuleta": "chuleta",
    }
    for token, objetivo in sinonimos.items():
        if re.search(rf"\b{token}\b", t):
            for p in productos:
                if objetivo in normalizar_texto(p.nombre):
                    return p

    # 3. Si solo dijo "carne" o "res", sugerir o tomar el corte más popular (molida o lomo)
    if re.search(r"\bcarne\b", t):
        for p in productos:
            if "molida" in normalizar_texto(p.nombre) or "lomo" in normalizar_texto(p.nombre):
                return p
        if productos:
            return productos[0]

    return None


def procesar_salida_inventario(
    establecimiento, texto_venta: str, valor_ingresado: Optional[Decimal] = None
) -> Tuple[Optional[Producto], Decimal, Decimal, Decimal, str]:
    """
    Interpreta el concepto y/o valor de una venta, descuenta el stock del producto
    y calcula el valor total si no fue provisto.

    Retorna:
      (producto, kilos_descontados, libras_descontadas, valor_final, info_formateada)
    """
    prod = buscar_producto_en_texto(establecimiento, texto_venta)
    if not prod:
        return None, Decimal("0"), Decimal("0"), valor_ingresado or Decimal("0"), ""

    kilos = parsear_peso(texto_venta)
    valor_final = valor_ingresado if (valor_ingresado and valor_ingresado > 1) else None

    # Si no hay peso explícito pero sí hay dinero, calcular peso en kilos
    if kilos is None and valor_final and prod.precio_kilo > 0:
        kilos = (valor_final / prod.precio_kilo).quantize(Decimal("0.01"))

    # Si hay peso explícito pero no había dinero (o era 1), calcular el dinero según precio del producto
    if kilos and not valor_final:
        valor_final = (kilos * prod.precio_kilo).quantize(Decimal("1"))

    if not kilos or kilos <= 0:
        return prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), ""

    # Descontar del inventario
    prod.stock_kilos = max(Decimal("0"), prod.stock_kilos - kilos)
    prod.save()

    libras = (kilos * Decimal("2")).quantize(Decimal("0.1"))
    info_formateada = (
        f"\n📦 *Inventario Actualizado:*\n"
        f"   • Producto: *{prod.nombre}*\n"
        f"   • Salida: -{kilos} Kg (-{libras} lb)\n"
        f"   • Stock restante: *{prod.stock_kilos} Kg* ({prod.stock_libras} lb)"
    )

    return prod, kilos, libras, valor_final or Decimal("0"), info_formateada


def revertir_salida_inventario(establecimiento, venta) -> str:
    """Restaura el inventario cuando una venta es anulada."""
    # Buscar si quedó rastro en la auditoría
    audit = Auditoria.objects.filter(
        entidad_afectada="venta", id_registro=venta.pk, accion__startswith="crear"
    ).first()

    prod = None
    kilos = None

    if audit and "prod=" in audit.valor_nuevo:
        m_prod = re.search(r"prod=([^|]+)", audit.valor_nuevo)
        if m_prod and m_prod.group(1) != "none":
            prod = Producto.objects.filter(
                establecimiento=establecimiento, nombre=m_prod.group(1).strip()
            ).first()

    if not prod:
        prod = buscar_producto_en_texto(establecimiento, venta.concepto)

    if prod:
        kilos = parsear_peso(venta.concepto)
        if not kilos and prod.precio_kilo > 0 and venta.valor > 0:
            kilos = (venta.valor / prod.precio_kilo).quantize(Decimal("0.01"))

        if kilos and kilos > 0:
            prod.stock_kilos += kilos
            prod.save()
            libras = (kilos * Decimal("2")).quantize(Decimal("0.1"))
            return f"\n🔄 *Inventario Revertido:* Se repusieron +{kilos} Kg (+{libras} lb) a *{prod.nombre}* (Stock: {prod.stock_kilos} Kg)."

    return ""
