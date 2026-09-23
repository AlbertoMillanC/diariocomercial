import re
from decimal import Decimal
from typing import Optional, Tuple
from .models import Producto, Auditoria, ItemPedido


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

    # 1. Búsqueda exacta del nombre en el texto o del texto en el nombre
    for p in productos:
        p_norm = normalizar_texto(p.nombre)
        if p_norm in t or (len(t) >= 4 and t in p_norm):
            return p

    # 2. Diccionario de sinónimos y términos populares de supermercado y carnicería
    sinonimos = {
        # Carnes
        "molida": "carne molida",
        "lomo": "lomo de res",
        "lomito": "lomo de res",
        "pechuga": "pechuga de pollo",
        "pierna pernil": "pierna pernil de pollo",
        "pernil": "pierna pernil de pollo",
        "muslo": "pierna pernil de pollo",
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
        # Abarrotes
        "arroz": "arroz blanco",
        "frijol": "frijol rojo",
        "lenteja": "lenteja",
        "garbanzo": "garbanzo",
        "azucar": "azucar blanco",
        "panela": "panela",
        "cafe": "cafe molido",
        "aceite": "aceite vegetal",
        "harina pan": "harina de maiz",
        "harina maiz": "harina de maiz",
        "harina trigo": "harina de trigo",
        "pasta": "pastas espagueti",
        "espagueti": "pastas espagueti",
        "sal": "sal refinada",
        "avena": "avena en hojuelas",
        # Lácteos y Huevos
        "huevo": "huevos campesinos",
        "huevos": "huevos campesinos",
        "queso": "queso campesino",
        "cuajada": "cuajada",
        "leche": "leche entera",
        "mantequilla": "mantequilla",
        "yogur": "yogur natural",
        # Fruver
        "papa pastusa": "papa pastusa",
        "papa criolla": "papa criolla",
        "papa": "papa pastusa",
        "platano": "platano verde",
        "tomate": "tomate chonto",
        "cebolla": "cebolla cabezona",
        "zanahoria": "zanahoria",
        "aguacate": "aguacate",
        "limon": "limon tahiti",
        "banano": "banano",
        "manzana": "manzana",
        "naranja": "naranja",
        # Bebidas
        "agua": "agua embotellada",
        "gaseosa": "gaseosa",
        "cerveza": "cerveza nacional",
        "jugo": "jugo de frutas",
        # Aseo
        "jabon polvo": "jabon en polvo",
        "jabon barra": "jabon de barra",
        "jabon bano": "jabon de bano",
        "jabon": "jabon en polvo",
        "detergente": "detergente liquido",
        "papel": "papel higienico",
        "cloro": "blanqueador cloro",
        "crema dental": "crema dental",
        "colgate": "crema dental",
    }
    for token, objetivo in sinonimos.items():
        if re.search(rf"\b{token}\b", t):
            for p in productos:
                if objetivo in normalizar_texto(p.nombre):
                    return p

    # 3. Coincidencia por palabras individuales
    palabras_texto = [
        w
        for w in t.split()
        if len(w) >= 4
        and w
        not in (
            "para",
            "este",
            "esta",
            "kilo",
            "libra",
            "gramo",
            "tenemos",
            "cuanto",
            "cuesta",
            "precio",
            "vendes",
        )
    ]
    for w in palabras_texto:
        for p in productos:
            if w in normalizar_texto(p.nombre):
                return p

    # 4. Si solo dijo "carne" o "res", tomar Carne molida o Carne para asar
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

    # Alerta inmediata de compras si el stock queda agotado o bajo
    if prod.stock_kilos <= Decimal("0"):
        registrar_o_actualizar_pedido(
            establecimiento=establecimiento,
            nombre_producto=prod.nombre,
            producto=prod,
            origen="agotado",
            cantidad_sugerida=Decimal("10.0") if prod.categoria in ("carnes", "abarrotes") else Decimal("5.0"),
            unidad="Kg",
            observacion="Agotado tras venta reciente",
        )
        info_formateada += (
            f"\n\n🚨 *ALERTA COMPRAR:* ¡Stock de *{prod.nombre}* AGOTADO (0 Kg restantes)!\n"
            f"   Se incluyó automáticamente en la lista de compras pendientes (`/comprar`)."
        )
    elif prod.stock_kilos <= Decimal("3.0"):
        registrar_o_actualizar_pedido(
            establecimiento=establecimiento,
            nombre_producto=prod.nombre,
            producto=prod,
            origen="stock_bajo",
            cantidad_sugerida=Decimal("5.0"),
            unidad="Kg",
            observacion=f"Stock bajo tras venta: {prod.stock_kilos} Kg",
        )
        info_formateada += (
            f"\n\n⚠️ *ALERTA COMPRAR:* Stock de *{prod.nombre}* bajo ({prod.stock_kilos} Kg restantes).\n"
            f"   Sugerido para pedido de reposición en `/comprar`."
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


def generar_lista_categoria(establecimiento, categoria: str) -> str:
    """Genera el mensaje con productos, existencias y precios de una categoría."""
    cat_aliases = {
        "carnes": ("carnes", "🥩 CARNES Y EMBUTIDOS"),
        "carne": ("carnes", "🥩 CARNES Y EMBUTIDOS"),
        "abarrotes": ("abarrotes", "🌾 VÍVERES Y ABARROTES"),
        "viveres": ("abarrotes", "🌾 VÍVERES Y ABARROTES"),
        "granos": ("abarrotes", "🌾 VÍVERES Y ABARROTES"),
        "lacteos": ("lacteos", "🧀 LÁCTEOS Y HUEVOS"),
        "huevos": ("lacteos", "🧀 LÁCTEOS Y HUEVOS"),
        "fruver": ("fruver", "🥬 FRUTAS Y VERDURAS"),
        "verduras": ("fruver", "🥬 FRUTAS Y VERDURAS"),
        "frutas": ("fruver", "🥬 FRUTAS Y VERDURAS"),
        "bebidas": ("bebidas", "🥤 BEBIDAS"),
        "aseo": ("aseo", "🧼 ASEO Y LIMPIEZA"),
    }
    cat_info = cat_aliases.get(categoria.lower().strip())
    if not cat_info:
        cat_key = categoria
        cat_titulo = f"📦 {categoria.upper()}"
    else:
        cat_key, cat_titulo = cat_info

    productos = Producto.objects.filter(
        establecimiento=establecimiento, categoria=cat_key, estado="activo"
    ).order_by("nombre")

    if not productos.exists():
        return f"ℹ️ No hay productos registrados en la categoría {cat_titulo}."

    total_kilos = sum((p.stock_kilos for p in productos), Decimal("0"))
    lineas = [
        f"{cat_titulo}",
        f"📍 _{establecimiento.nombre}_ | Variedades: {productos.count()} | Total: {total_kilos:,.1f} Kg\n",
    ]
    for p in productos:
        info_precio = f"💰 Kilo: ${p.precio_kilo:,.0f} | Libra: ${p.precio_libra:,.0f}"
        if p.precio_gramo >= Decimal("1"):
            info_precio += f" | Gramo: ${p.precio_gramo}"
        lineas.append(
            f"• *{p.nombre}*\n"
            f"  📦 Stock: *{p.stock_kilos:,.2f} Kg* ({p.stock_libras} lb)\n"
            f"  {info_precio}"
        )

    primer_nombre = productos.first().nombre.lower() if productos.exists() else "producto"
    lineas.append(f"\n💡 _Para vender, escribe ej:_ `20 mil {primer_nombre}`")
    return "\n".join(lineas)


def generar_resumen_general_categorias(establecimiento) -> str:
    """Genera el resumen general de inventario por departamentos/grupos."""
    productos = Producto.objects.filter(establecimiento=establecimiento, estado="activo")
    if not productos.exists():
        return "📦 No hay productos registrados en el inventario."

    total_kilos = sum((p.stock_kilos for p in productos), Decimal("0"))
    valor_total = sum((p.stock_kilos * p.precio_kilo for p in productos), Decimal("0"))

    categorias_def = [
        ("carnes", "🥩 Carnes y Embutidos", "/carnes"),
        ("abarrotes", "🌾 Víveres y Abarrotes", "/abarrotes"),
        ("lacteos", "🧀 Lácteos y Huevos", "/lacteos"),
        ("fruver", "🥬 Frutas y Verduras", "/fruver"),
        ("bebidas", "🥤 Bebidas", "/bebidas"),
        ("aseo", "🧼 Aseo y Limpieza", "/aseo"),
    ]

    lineas = [
        "🏪 *INVENTARIO GENERAL POR DEPARTAMENTOS*",
        f"📍 _{establecimiento.nombre}_\n",
        f"📊 *Existencias Globales:* {total_kilos:,.1f} Kg",
        f"💰 *Valor Total Inventario:* ${valor_total:,.0f} COP",
        f"🏷️ *Catálogo Activo:* {productos.count()} productos en 6 grupos\n",
        "📂 *CONSULTAR POR GRUPOS:*",
    ]

    for cat_key, cat_nombre, cmd in categorias_def:
        cat_prods = productos.filter(categoria=cat_key)
        cat_kilos = sum((p.stock_kilos for p in cat_prods), Decimal("0"))
        cat_val = sum((p.stock_kilos * p.precio_kilo for p in cat_prods), Decimal("0"))
        lineas.append(
            f"• *{cat_nombre}* ({cmd})\n"
            f"   {cat_prods.count()} productos | {cat_kilos:,.1f} Kg | ${cat_val:,.0f} COP"
        )

    lineas.append(
        "\n💬 *O pregúntame directamente en el chat:*\n"
        "• _¿tenemos carne?_\n"
        "• _¿tenemos tocino?_\n"
        "• _¿que abarrotes hay?_\n"
        "• _¿cuanto vale el arroz?_"
    )
    return "\n".join(lineas)


def consultar_producto_o_categoria(establecimiento, texto: str) -> Optional[str]:
    """
    Detecta si el mensaje es una pregunta en lenguaje natural sobre inventario:
      - 'que carnes tengo', 'que abarrotes hay', 'que lacteos hay', 'que verduras hay'
      - 'tenemos carne...', 'tenemos tocino...', 'tenemos arroz?', 'tenemos huevos'
      - 'hay tocino?', 'hay leche?', 'queda queso?'
      - 'cuanto vale el arroz', 'cuanto cuesta la pechuga', 'precio del tocino'
    Retorna la respuesta formateada o None si no corresponde a una consulta de inventario.
    """
    t = normalizar_texto(texto).strip()
    t_limpio = re.sub(r"[¿\?\.!]", "", t).strip()

    # 1. Preguntas de categorías completas: "que carnes tengo", "que abarrotes hay"
    m_cat = re.search(
        r"^(?:que|cuales|ver|mostrar|lista de)\s+(carnes?|abarrotes|viveres|granos|lacteos|huevos|frutas?|verduras?|fruver|bebidas?|aseo)(?:\s+(?:hay|tengo|tenemos|vendes|venden))?$",
        t_limpio,
    )
    if m_cat:
        cat_pedida = m_cat.group(1)
        return generar_lista_categoria(establecimiento, cat_pedida)

    # 2. Preguntas sobre si hay un producto o cuánto cuesta:
    # "tenemos tocino", "tenemos carne", "hay leche", "queda arroz", "tienen huevos"
    # "cuanto vale el tocino", "cuanto cuesta la carne", "precio del arroz"
    patron_pregunta = r"^(?:tenemos|hay|tienen|queda|disponible|cuanto\s+(?:cuesta|vale)|precio(?:\s+de)?)\s+(?:el\s+|la\s+|los\s+|las\s+|de\s+)?(.+)$"
    m_prod = re.match(patron_pregunta, t_limpio)

    termino_busqueda = None
    if m_prod:
        termino_busqueda = m_prod.group(1).strip()
    elif t.endswith("?") and not any(k in t for k in ("venta", "compra", "retencion")):
        termino_busqueda = t_limpio
    elif not any(char.isdigit() for char in t_limpio):
        palabras = t_limpio.split()
        comandos_excluidos = (
            "venta", "vendi", "compra", "retencion", "anular", "resumen",
            "consolidado", "ayuda", "historial", "start", "hola", "hoy", "caja",
            "comprar", "pedido", "pedidos", "faltantes"
        )
        if 1 <= len(palabras) <= 3 and not any(k in t_limpio for k in comandos_excluidos):
            termino_busqueda = t_limpio

    if not termino_busqueda:
        return None

    # Si preguntó genéricamente "tenemos carne" o "hay carne", mostrar cortes
    if termino_busqueda in ("carne", "carnes"):
        return generar_lista_categoria(establecimiento, "carnes")
    if termino_busqueda in ("abarrote", "abarrotes", "viveres", "grano", "granos"):
        return generar_lista_categoria(establecimiento, "abarrotes")
    if termino_busqueda in ("lacteo", "lacteos"):
        return generar_lista_categoria(establecimiento, "lacteos")
    if termino_busqueda in ("fruta", "frutas", "verdura", "verduras", "fruver"):
        return generar_lista_categoria(establecimiento, "fruver")
    if termino_busqueda in ("bebida", "bebidas"):
        return generar_lista_categoria(establecimiento, "bebidas")
    if termino_busqueda in ("aseo", "limpieza"):
        return generar_lista_categoria(establecimiento, "aseo")

    # Buscar producto específico
    prod = buscar_producto_en_texto(establecimiento, termino_busqueda)
    if prod:
        estado_icono = "✅" if prod.stock_kilos > 0 else "⚠️"
        estado_msg = "Disponible en tienda" if prod.stock_kilos > 0 else "Agotado temporalmente"
        info_precio = f"• Kilo: ${prod.precio_kilo:,.0f} COP\n   • Libra: ${prod.precio_libra:,.0f} COP"
        if prod.precio_gramo >= Decimal("1"):
            info_precio += f"\n   • Gramo: ${prod.precio_gramo} COP"

        icono_cat = {
            "carnes": "🥩",
            "abarrotes": "🌾",
            "lacteos": "🧀",
            "fruver": "🥬",
            "bebidas": "🥤",
            "aseo": "🧼",
            "otros": "📦",
        }.get(prod.categoria, "📦")

        alerta_adicional = ""
        if prod.stock_kilos <= Decimal("0"):
            registrar_o_actualizar_pedido(
                establecimiento=establecimiento,
                nombre_producto=prod.nombre,
                producto=prod,
                origen="agotado",
                cantidad_sugerida=Decimal("10.0") if prod.categoria in ("carnes", "abarrotes") else Decimal("5.0"),
                unidad="Kg",
                observacion="Consultado por cliente y agotado",
            )
            alerta_adicional = (
                f"\n\n🚨 *ALERTA COMPRAR:* No hay existencias de *{prod.nombre}* (0 Kg).\n"
                f"📝 Se incluyó automáticamente en la lista de compras pendientes (`/comprar`)."
            )
        elif prod.stock_kilos <= Decimal("3.0"):
            registrar_o_actualizar_pedido(
                establecimiento=establecimiento,
                nombre_producto=prod.nombre,
                producto=prod,
                origen="stock_bajo",
                cantidad_sugerida=Decimal("5.0"),
                unidad="Kg",
                observacion=f"Stock bajo consultado: {prod.stock_kilos} Kg",
            )
            alerta_adicional = (
                f"\n\n⚠️ *ALERTA:* Queda poco stock ({prod.stock_kilos} Kg restantes).\n"
                f"📝 Sugerido para reposición en `/comprar`."
            )

        return (
            f"{icono_cat} *Consulta de Producto: {prod.nombre}*\n"
            f"📍 _{establecimiento.nombre}_\n\n"
            f"📦 *Existencias actuales:* *{prod.stock_kilos:,.2f} Kg* ({prod.stock_libras} lb / {prod.stock_gramos:,} g)\n"
            f"💰 *Precios vigentes:*\n   {info_precio}\n\n"
            f"{estado_icono} *Estado:* _{estado_msg}_{alerta_adicional}"
        )

    # Si no se encontró en el catálogo: agregarlo a productos solicitados por clientes
    nombre_limpio = re.sub(
        r"^(de\s+|el\s+|la\s+|los\s+|las\s+|un\s+|una\s+|unos\s+|unas\s+)", "", termino_busqueda
    ).strip().capitalize()

    if len(nombre_limpio) >= 3:
        item = registrar_o_actualizar_pedido(
            establecimiento=establecimiento,
            nombre_producto=nombre_limpio,
            producto=None,
            origen="solicitado",
            cantidad_sugerida=Decimal("5.0"),
            unidad="Kg",
            observacion="Preguntado por cliente / no existe en catálogo",
        )
        veces_txt = f"{item.veces_solicitado} vez" if item.veces_solicitado == 1 else f"{item.veces_solicitado} veces"
        return (
            f"❌ No tenemos *'{nombre_limpio}'* en el catálogo actual del supermercado.\n\n"
            f"📝 *ALERTA:* Agregado a la lista de compras futuras / pedido formal.\n"
            f"📊 Solicitado por clientes: *{veces_txt}*.\n\n"
            f"💡 Escribe `/comprar` para ver todos los productos pendientes por pedir."
        )

    return (
        f"🔍 No encontré *'{termino_busqueda}'* en el catálogo del supermercado.\n\n"
        "Puedes escribir `/inventario` para ver los departamentos disponibles o probar:\n"
        "• `/carnes`\n• `/abarrotes`\n• `/lacteos`\n• `/fruver`\n• `/bebidas`\n• `/aseo`"
    )


def inferir_categoria(texto: str) -> str:
    """Infiere la categoría adecuada para un producto nuevo no registrado."""
    t = normalizar_texto(texto)
    if any(w in t for w in ("salchicha", "salchichon", "jamon", "mortadela", "carne", "pollo", "cerdo", "res", "tocino", "costilla", "morcilla", "embutido", "chorizo")):
        return "carnes"
    if any(w in t for w in ("arroz", "frijol", "lenteja", "garbanzo", "harina", "aceite", "azucar", "sal", "cafe", "panela", "pasta", "espagueti", "galleta", "avena", "arepa", "atun", "sardina")):
        return "abarrotes"
    if any(w in t for w in ("leche", "queso", "yogurt", "yogur", "cuajada", "mantequilla", "crema", "huevo", "huevos", "kumis", "suero")):
        return "lacteos"
    if any(w in t for w in ("papa", "cebolla", "tomate", "platano", "limon", "aguacate", "zanahoria", "fruta", "verdura", "manzana", "pera", "banano", "naranja", "cilantro", "guayaba", "fresa", "mango", "pina")):
        return "fruver"
    if any(w in t for w in ("agua", "gaseosa", "jugo", "cerveza", "refresco", "soda", "cola", "pony", "malta", "vino", "aguardiente")):
        return "bebidas"
    if any(w in t for w in ("jabon", "detergente", "cloro", "blanqueador", "papel", "higienico", "crema dental", "shampoo", "limpido", "suavizante", "cepillo", "toalla", "escoba", "trapero")):
        return "aseo"
    return "otros"


def registrar_o_actualizar_pedido(
    establecimiento,
    nombre_producto: str,
    producto: Optional[Producto] = None,
    origen: str = "agotado",
    cantidad_sugerida: Decimal = Decimal("5.0"),
    unidad: str = "Kg",
    observacion: str = "",
) -> ItemPedido:
    """
    Registra o actualiza un ítem en la lista de compras/pedidos pendientes.
    Si ya existe un pedido pendiente con el mismo producto o nombre, incrementa
    el número de veces solicitado y actualiza su observación.
    """
    nombre_limpio = nombre_producto.strip()
    item = None
    if producto:
        item = ItemPedido.objects.filter(
            establecimiento=establecimiento, producto=producto, estado="pendiente"
        ).first()
    if not item:
        pendientes = ItemPedido.objects.filter(
            establecimiento=establecimiento, estado="pendiente"
        )
        for p in pendientes:
            if normalizar_texto(p.nombre_producto) == normalizar_texto(nombre_limpio):
                item = p
                break

    if item:
        item.veces_solicitado += 1
        if observacion and observacion not in item.observacion:
            item.observacion = f"{item.observacion} | {observacion}".strip(" |")
        if origen == "agotado":
            item.origen = "agotado"
        item.save()
        return item

    categoria = producto.categoria if producto else inferir_categoria(nombre_limpio)
    item = ItemPedido.objects.create(
        establecimiento=establecimiento,
        producto=producto,
        nombre_producto=nombre_limpio.capitalize(),
        categoria=categoria,
        cantidad_sugerida=cantidad_sugerida,
        unidad=unidad,
        origen=origen,
        veces_solicitado=1,
        observacion=observacion,
        estado="pendiente",
    )
    return item


def sincronizar_productos_agotados(establecimiento):
    """Sincroniza automáticamente productos con stock <= 0 a la lista de compras."""
    if not establecimiento:
        return
    agotados = Producto.objects.filter(
        establecimiento=establecimiento, stock_kilos__lte=0, estado="activo"
    )
    for p in agotados:
        registrar_o_actualizar_pedido(
            establecimiento=establecimiento,
            nombre_producto=p.nombre,
            producto=p,
            origen="agotado",
            cantidad_sugerida=Decimal("10.0") if p.categoria in ("carnes", "abarrotes") else Decimal("5.0"),
            unidad="Kg",
            observacion="Stock en 0 Kg",
        )


def generar_lista_compras(establecimiento) -> str:
    """
    Genera el reporte de compras pendientes y pedido formal para Telegram.
    Divide entre:
      - Inventario Agotado / Por Reponer (0 Kg)
      - Stock Bajo (Alerta preventiva)
      - Solicitados por Clientes (Nuevos productos que no existen)
      - Manuales
    """
    if not establecimiento:
        return "⚠️ No hay un establecimiento configurado."

    sincronizar_productos_agotados(establecimiento)

    items = ItemPedido.objects.filter(
        establecimiento=establecimiento, estado="pendiente"
    ).order_by("-fecha_actualizacion")

    if not items.exists():
        return (
            "✅ *¡Todo al día con el inventario!*\n\n"
            "No hay productos agotados ni pedidos pendientes por comprar en este momento.\n\n"
            "💡 _Si un cliente pregunta por algo nuevo (ej: salchichas), escríbelo aquí y se agregará automáticamente a compras futuras._"
        )

    agotados = [i for i in items if i.origen == "agotado"]
    stock_bajo = [i for i in items if i.origen == "stock_bajo"]
    solicitados = [i for i in items if i.origen == "solicitado"]
    manuales = [i for i in items if i.origen == "manual"]

    lineas = [
        "🛒 *LISTA DE COMPRAS Y PEDIDO FORMAL*",
        f"📍 _{establecimiento.nombre}_ | Pendientes: {items.count()}\n",
    ]

    if agotados:
        lineas.append("🔴 *INVENTARIO AGOTADO (URGENTE):*")
        for i in agotados:
            stock_info = f" (Stock: {i.producto.stock_kilos} Kg)" if i.producto else ""
            lineas.append(f"• *{i.nombre_producto}*{stock_info} — Reponer aprox. {i.cantidad_sugerida:,.0f} {i.unidad}")
        lineas.append("")

    if stock_bajo:
        lineas.append("🟡 *STOCK BAJO (ALERTA PREVENTIVA):*")
        for i in stock_bajo:
            stock_info = f" ({i.producto.stock_kilos} Kg restantes)" if i.producto else ""
            lineas.append(f"• *{i.nombre_producto}*{stock_info} — Sugerido pedir: {i.cantidad_sugerida:,.0f} {i.unidad}")
        lineas.append("")

    if solicitados:
        lineas.append("🔵 *PRODUCTOS SOLICITADOS POR CLIENTES (No en tienda):*")
        for i in solicitados:
            veces_txt = f"{i.veces_solicitado} vez" if i.veces_solicitado == 1 else f"{i.veces_solicitado} veces"
            lineas.append(f"• *{i.nombre_producto}* (Cat: {i.get_categoria_display()}) — Preguntado {veces_txt}")
        lineas.append("")

    if manuales:
        lineas.append("⚪ *AGREGADOS MANUALMENTE:*")
        for i in manuales:
            obs = f" ({i.observacion})" if i.observacion else ""
            lineas.append(f"• *{i.nombre_producto}* — {i.cantidad_sugerida:,.0f} {i.unidad}{obs}")
        lineas.append("")

    lineas.append(
        "🌐 *Gestión en Django Web:*\n"
        "👉 Administra o marca como comprados en: `http://127.0.0.1:8001/pedidos/`\n\n"
        "💡 _Para registrar compras que salgan del libro de caja escribe:_ `Compra <monto> <proveedor>`"
    )

    return "\n".join(lineas)

