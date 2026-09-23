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


def parsear_dinero(texto: str) -> Tuple[Optional[Decimal], str]:
    """
    Detecta y extrae montos en pesos colombianos como:
      - '40 mil' o '40mil' -> 40000
      - '40k' -> 40000
      - '40 lucas' -> 40000
      - '40.000' o '40000' o '$40000' -> 40000
    Retorna: (valor_decimal, texto_limpio_sin_cifra)
    """
    t = normalizar_texto(texto)

    # 1. Patrón con 'mil', 'k', 'lucas': ej "40 mil", "40mil", "40k", "40 lucas", "12.5 mil"
    m_mil = re.search(r"(?:\$|\b)(\d+(?:[\.,]\d+)?)\s*(?:mil|k|lucas)\b", t)
    if m_mil:
        num_str = m_mil.group(1).replace(",", ".")
        try:
            val = (Decimal(num_str) * Decimal("1000")).quantize(Decimal("1"))
            texto_restante = t.replace(m_mil.group(0), " ").strip()
            texto_restante = re.sub(r"^(de\s+|para\s+)", "", texto_restante).strip()
            return val, texto_restante
        except Exception:
            pass

    # 2. Cifra de dinero con puntos o formato numérico >= 3 dígitos: ej "$40.000", "40000"
    m_cifra = re.search(r"(?:\$|\b)(\d{1,3}(?:\.\d{3})+|\d{3,})\b", t)
    if m_cifra:
        raw = m_cifra.group(1).replace(".", "").replace(",", "")
        val = Decimal(raw)
        texto_restante = t.replace(m_cifra.group(0), " ").strip()
        texto_restante = re.sub(r"^(de\s+|para\s+)", "", texto_restante).strip()
        return val, texto_restante

    return None, t


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

    # Fracciones comunes de libra / kilo
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
                return cant.quantize(Decimal("0.001"))
            elif "l" in unidad:
                return (cant * Decimal("0.5")).quantize(Decimal("0.001"))
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

    # 2. Búsqueda por palabras clave significativas de carnicería
    sinonimos = {
        "molida": "carne molida",
        "lomo": "lomo de res",
        "lomito": "lomo de res",
        "pechuga": "pechuga de pollo",
        "pierna pernil": "pierna pernil",
        "pernil": "pierna pernil",
        "muslo": "pierna pernil",
        "costilla de res": "costilla de res",
        "costilla de cerdo": "costilla de cerdo",
        "costilla": "costilla de cerdo",
        "costillitas": "costilla de cerdo",
        "sobrebarriga": "sobrebarriga",
        "falda": "sobrebarriga",
        "tocino": "tocino",
        "panceta": "tocino",
        "chicharron": "tocino",
        "chuleta": "chuleta de cerdo",
        "bistec": "carne para asar",
        "asar": "carne para asar",
        "chorizo": "chorizo",
    }
    for token, objetivo in sinonimos.items():
        if re.search(rf"\b{token}\b", t):
            for p in productos:
                if objetivo in normalizar_texto(p.nombre):
                    return p

    # 3. Si solo dijo "carne" o "res", tomar Carne molida o Carne para asar
    if re.search(r"\bcarne\b", t):
        for p in productos:
            if "carne molida" in normalizar_texto(p.nombre) or "carne para asar" in normalizar_texto(p.nombre):
                return p
        if productos:
            return productos[0]

    return None


def procesar_salida_inventario(
    establecimiento, texto_venta: str, valor_ingresado: Optional[Decimal] = None
) -> Tuple[Optional[Producto], Decimal, Decimal, Decimal, str]:
    """
    Interpreta el concepto y/o valor de una venta, calcula los gramos exactos
    según el precio por gramo/kilo, descuenta el stock del producto
    y calcula el valor monetario si no fue provisto.

    Retorna:
      (producto, kilos_descontados, libras_descontadas, valor_final, info_formateada)
    """
    prod = buscar_producto_en_texto(establecimiento, texto_venta)
    if not prod:
        return None, Decimal("0"), Decimal("0"), valor_ingresado or Decimal("0"), ""

    # Extraer monto de dinero si no vino en valor_ingresado
    val_extraido, _ = parsear_dinero(texto_venta)
    valor_final = valor_ingresado if (valor_ingresado and valor_ingresado > 1) else val_extraido

    kilos = parsear_peso(texto_venta)

    # 1. Caso Venta por Dinero (ej: $40.000 o "40 mil carne molida"):
    # Se calcula la cantidad exacta en gramos con el precio por gramo del corte
    if kilos is None and valor_final and prod.precio_gramo > 0:
        gramos = int((Decimal(valor_final) / prod.precio_gramo).quantize(Decimal("1")))
        kilos = (Decimal(gramos) / Decimal("1000")).quantize(Decimal("0.001"))
    elif kilos is not None:
        # 2. Caso Venta por Peso explícito (ej: 1 libra, 2 kilos, 500g):
        gramos = int((kilos * Decimal("1000")).quantize(Decimal("1")))
        if not valor_final and prod.precio_gramo > 0:
            valor_final = (Decimal(gramos) * prod.precio_gramo).quantize(Decimal("1"))
    else:
        gramos = 0

    if not kilos or kilos <= 0:
        return prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), ""

    # Descontar del inventario con precisión de gramos (3 decimales de Kilo)
    prod.stock_kilos = max(Decimal("0"), prod.stock_kilos - kilos)
    prod.save()

    libras = (Decimal(gramos) / Decimal("500")).quantize(Decimal("0.01"))
    gramos_str = f"{gramos:,.0f}".replace(",", ".")
    kilos_str = f"{kilos:.3f}".rstrip("0").rstrip(".")

    info_formateada = (
        f"\n📦 *Inventario Actualizado (Cálculo Exacto):*\n"
        f"   • Producto: *{prod.nombre}*\n"
        f"   • Salida vendida: *{gramos_str} gramos* ({kilos_str} Kg / {libras} lb)\n"
        f"   • Stock restante: *{prod.stock_kilos} Kg* ({prod.stock_libras} lb / {prod.stock_gramos:,} g)"
    )

    return prod, kilos, libras, valor_final or Decimal("0"), info_formateada


def revertir_salida_inventario(establecimiento, venta) -> str:
    """Restaura el inventario cuando una venta es anulada."""
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
        if audit and "kilos=" in audit.valor_nuevo:
            m_k = re.search(r"kilos=([\d\.]+)", audit.valor_nuevo)
            if m_k:
                kilos = Decimal(m_k.group(1))

        if not kilos:
            kilos = parsear_peso(venta.concepto)
            if not kilos and prod.precio_gramo > 0 and venta.valor > 0:
                gramos = int((Decimal(venta.valor) / prod.precio_gramo).quantize(Decimal("1")))
                kilos = (Decimal(gramos) / Decimal("1000")).quantize(Decimal("0.001"))

        if kilos and kilos > 0:
            prod.stock_kilos += kilos
            prod.save()
            gramos = int(kilos * 1000)
            libras = (Decimal(gramos) / Decimal("500")).quantize(Decimal("0.01"))
            return f"\n🔄 *Inventario Revertido:* Se repusieron +{gramos:,} g (+{kilos} Kg / +{libras} lb) a *{prod.nombre}* (Stock: {prod.stock_kilos} Kg)."

    return ""
