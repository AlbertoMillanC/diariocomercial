import re
from decimal import Decimal
from typing import Optional, Tuple
from django.db import transaction
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
    m_cifra = re.search(
        r"(?:\$|\b)(\d{1,3}(?:\.\d{3})+|\d{3,})\b(?!\s*(?:mililitros?|ml|cc|gramos?|gr|g|kilos?|kg|libras?|lb|litros?|lts?|lt|unidades?|und|paquetes?|pqts?|cajas?|cubetas?|botellas?)\b)",
        t,
    )
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


class ResultadoSalidaInventario(tuple):
    """
    Tupla de 5 elementos compatible hacia atrás:
      (prod, cantidad_descontada, detalle_secundario, valor_final, info_formateada)
    con atributos ricos para unidades de medida (gramos, unidades, ml) y concepto de ticket.
    """
    producto: Optional[Producto]
    kilos_descontados: Decimal
    libras_descontadas: Decimal
    valor_final: Decimal
    info_formateada: str
    cantidad: Decimal
    unidad_medida: str
    concepto_ticket: str
    bloqueado_sin_stock: bool
    motivo_bloqueo: str

    def __new__(
        cls,
        prod: Optional[Producto],
        cantidad_descontada: Decimal,
        detalle_secundario: Decimal,
        valor_final: Decimal,
        info_formateada: str,
        cantidad: Decimal = Decimal("0"),
        unidad_medida: str = "und",
        concepto_ticket: str = "",
        bloqueado_sin_stock: bool = False,
        motivo_bloqueo: str = "",
    ):
        obj = super().__new__(cls, (prod, cantidad_descontada, detalle_secundario, valor_final, info_formateada))
        obj.producto = prod
        obj.kilos_descontados = cantidad_descontada
        obj.libras_descontadas = detalle_secundario
        obj.valor_final = valor_final
        obj.info_formateada = info_formateada
        obj.cantidad = cantidad
        obj.unidad_medida = unidad_medida
        obj.concepto_ticket = concepto_ticket
        obj.bloqueado_sin_stock = bloqueado_sin_stock
        obj.motivo_bloqueo = motivo_bloqueo
        return obj


def parsear_volumen(texto: str) -> Optional[Tuple[Decimal, str]]:
    """
    Extrae el volumen en MILILITROS (ml) de una frase en español.
    Retorna: (cantidad_ml, unidad_str) o None
    Ejemplos:
      '500 ml aceite' -> (Decimal('500'), 'ml')
      '500 mililitros' -> (Decimal('500'), 'ml')
      '1 litro leche' -> (Decimal('1000'), 'ml')
      'un litro leche' -> (Decimal('1000'), 'ml')
      'medio litro gaseosa' -> (Decimal('500'), 'ml')
      '1.5 lt agua' -> (Decimal('1500'), 'ml')
      '250 cc crema' -> (Decimal('250'), 'ml')
    """
    t = normalizar_texto(texto)
    # Fracciones de litro
    if "medio litro" in t or "1/2 litro" in t or "1/2 lt" in t or "1/2 l" in t:
        return Decimal("500"), "ml"
    if "cuarto de litro" in t or "1/4 litro" in t or "1/4 lt" in t:
        return Decimal("250"), "ml"
    if "3/4 litro" in t or "3/4 lt" in t:
        return Decimal("750"), "ml"

    palabras_num = {
        "un": Decimal("1"), "una": Decimal("1"), "dos": Decimal("2"), "tres": Decimal("3"),
        "cuatro": Decimal("4"), "cinco": Decimal("5"), "medio": Decimal("0.5")
    }
    for palabra, num in palabras_num.items():
        m = re.search(rf"\b{palabra}\s*(litros?|lts?|lt|l)\b", t)
        if m:
            return (num * Decimal("1000")).quantize(Decimal("1")), "ml"

    # 500 ml, 250 cc, etc.
    m_ml = re.search(r"(\d+(?:[\.,]\d+)?)\s*(mililitros?|ml|cc)\b", t)
    if m_ml:
        cant = Decimal(m_ml.group(1).replace(",", "."))
        return cant.quantize(Decimal("1")), "ml"

    # 1.5 lt, 2 litros, 1 l
    m_lt = re.search(r"(\d+(?:[\.,]\d+)?)\s*(litros?|lts?|lt)\b", t)
    if m_lt:
        cant = Decimal(m_lt.group(1).replace(",", "."))
        return (cant * Decimal("1000")).quantize(Decimal("1")), "ml"

    return None


def parsear_unidades(texto: str) -> Optional[Tuple[Decimal, str]]:
    """
    Detecta y extrae cantidades de unidades discretas (und, cajas, paquetes, etc.).
    Retorna: (cantidad, unidad_str) o None
    Ejemplos:
      '3 cervezas' -> (Decimal('3'), 'und')
      '5 panes' -> (Decimal('5'), 'und')
      '10 pan' -> (Decimal('10'), 'und')
      '2 cajas de leche' -> (Decimal('2'), 'cajas')
      '1 cubeta de huevos' -> (Decimal('1'), 'cubeta')
      'dos cervezas' -> (Decimal('2'), 'und')
      'un pan' -> (Decimal('1'), 'und')
    """
    t = normalizar_texto(texto)
    # Limpiar dinero primero para no confundir con montos
    t_clean = re.sub(r"(?:\$|\b)(\d+(?:[\.,]\d+)?)\s*(?:mil|k|lucas)\b", " ", t)
    t_clean = re.sub(r"(?:\$|\b)(\d{1,3}(?:\.\d{3})+|\d{4,})\b", " ", t_clean)
    # Limpiar peso y volumen
    t_clean = re.sub(r"\b\d+(?:[\.,]\d+)?\s*(?:libras?|lb|kilos?|kg|gramos?|gr|g|mililitros?|ml|cc|litros?|lts?|lt)\b", " ", t_clean)
    t_clean = re.sub(r"\b(?:media\s+libra|medio\s+kilo|medio\s+litro|1/2\s*lb|1/2\s*kilo|1/4\s*lb)\b", " ", t_clean)

    # 1. Unidades con sufijo explícito: 3 und, 5 unidades, 2 pqt, 2 paquetes, 1 cubeta, 2 cajas, 3 latas, 2 botellas
    m_exp = re.search(r"\b(\d+)\s*(unidades|unidad|und|pqt|paquetes?|cajas?|cubetas?|latas?|botellas?|bolsas?|tarros?)\b", t_clean)
    if m_exp:
        cant = Decimal(m_exp.group(1))
        unidad = m_exp.group(2)
        u = "und" if unidad in ("unidades", "unidad", "und") else unidad
        return cant, u

    # 2. Palabras numéricas seguidas de sustantivo: 'un', 'una', 'dos', 'tres', etc.
    palabras_num = {
        "un": Decimal("1"), "una": Decimal("1"), "dos": Decimal("2"), "tres": Decimal("3"),
        "cuatro": Decimal("4"), "cinco": Decimal("5"), "seis": Decimal("6"), "siete": Decimal("7"),
        "ocho": Decimal("8"), "nueve": Decimal("9"), "diez": Decimal("10"), "doce": Decimal("12"),
    }
    for pal, val in palabras_num.items():
        m_pal = re.search(rf"\b{pal}\s+([a-z]+)\b", t_clean)
        if m_pal and m_pal.group(1) not in ("mil", "kilo", "kilos", "kg", "libra", "libras", "lb", "litro", "litros", "lt", "gramo", "gramos", "g", "ml", "dian", "tipo", "codigo", "doc", "documento"):
            return val, "und"

    # 3. Número al inicio o antes de la palabra del producto: '3 cervezas', '5 panes', '20 pan', '10 huevos'
    # No debe ser un número de cédula (longitud >= 5)
    m_num = re.search(r"\b([1-9]\d{0,2})\s+([a-z]+)\b", t_clean)
    if m_num:
        palabra_sig = m_num.group(2)
        if palabra_sig not in ("mil", "k", "lucas", "pesos", "de", "para", "con", "dian", "tipo", "codigo", "doc", "documento"):
            cant = Decimal(m_num.group(1))
            return cant, "und"

    return None


def buscar_producto_en_texto(establecimiento, texto: str) -> Optional[Producto]:
    """Busca el producto de inventario más afín al texto ingresado."""
    t = normalizar_texto(texto)
    productos = list(
        Producto.objects.filter(establecimiento=establecimiento, estado="activo")
    )
    # Ordenar por longitud de nombre descendente para priorizar "carne molida" sobre "carne"
    productos.sort(key=lambda p: len(p.nombre), reverse=True)

    # 0. Búsqueda prioritaria por Código Rápido / Código de Barras / SKU (ej: 2311412413 o 101)
    for p in productos:
        if p.codigo_barras:
            cb_norm = normalizar_texto(p.codigo_barras)
            if cb_norm and re.search(rf"\b{re.escape(cb_norm)}\b", t):
                return p

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

    # 3. Coincidencia por palabras individuales y singular/plural (ej: cervezas -> cerveza, panes -> pan)
    palabras_texto = [
        re.sub(r"[^a-z0-9]", "", w)
        for w in t.split()
        if len(w) >= 3
        and w not in (
            "mil", "con", "para", "este", "esta", "kilo", "kilos", "libra", "libras",
            "gramo", "gramos", "litro", "litros", "tenemos", "cuanto", "cuesta",
            "precio", "vendes", "tipo", "dian", "codigo", "doc", "documento", "por", "que", "los", "las", "und"
        )
    ]
    for w in palabras_texto:
        w_sing = w.rstrip("s")
        for p in productos:
            p_norm = normalizar_texto(p.nombre)
            p_words = [re.sub(r"[^a-z0-9]", "", pw).rstrip("s") for pw in p_norm.split()]
            if w_sing in p_words or (len(w_sing) >= 4 and w_sing in p_norm):
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
) -> ResultadoSalidaInventario:
    """
    Interpreta el concepto y/o valor de una venta.
    Distingue automáticamente según el tipo de producto o solicitud del usuario:
      1. PESO: Gramos, Kilos, Libras.
      2. LÍQUIDOS: Mililitros (ml), Litros (lt).
      3. UNIDADES: Cantidad de piezas, paquetes, latas, und.
    Descuenta el inventario en la escala correcta y formatea el concepto exacto para ticket/factura.
    """
    prod = buscar_producto_en_texto(establecimiento, texto_venta)
    val_extraido, texto_sin_dinero = parsear_dinero(texto_venta)
    valor_final = valor_ingresado if (valor_ingresado and valor_ingresado > 1) else val_extraido

    if not prod:
        vol = parsear_volumen(texto_venta)
        und = parsear_unidades(texto_venta)
        peso = parsear_peso(texto_venta)
        nombre_limpio = (texto_sin_dinero[:120].strip() or "Venta general").capitalize()

        cant = Decimal("1.000")
        u_med = "und"
        concepto_ticket = nombre_limpio

        if vol:
            cant = vol[0]
            u_med = "ml"
            concepto_ticket = f"{nombre_limpio} ({int(cant):,} ml)".replace(",", ".")
        elif und:
            cant = und[0]
            u_med = und[1]
            concepto_ticket = f"{nombre_limpio} ({int(cant)} {u_med})"
        elif peso:
            g = int(peso * Decimal("1000"))
            cant = Decimal(g)
            u_med = "g"
            concepto_ticket = f"{nombre_limpio} ({g:,} g)".replace(",", ".")

        return ResultadoSalidaInventario(
            None, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), "",
            cantidad=cant, unidad_medida=u_med, concepto_ticket=concepto_ticket
        )

    # Determinar si el producto se gestiona por Peso, Líquido o Unidad
    es_peso = prod.es_peso()
    es_liquido = prod.es_liquido()
    es_unidad = prod.es_unidad()

    kilos_peso = parsear_peso(texto_venta)
    volumen_ml = parsear_volumen(texto_venta)
    unidades_und = parsear_unidades(texto_venta)

    # -------------------------------------------------------------
    # CASO 1: PESO (Carnes, Fruver, etc. medidos por Gramos/Kg/lb)
    # -------------------------------------------------------------
    if es_peso or (kilos_peso is not None and not es_liquido and not es_unidad):
        if kilos_peso is None and valor_final and prod.precio_gramo > 0:
            gramos = int((Decimal(valor_final) / prod.precio_gramo).quantize(Decimal("1")))
            kilos = (Decimal(gramos) / Decimal("1000")).quantize(Decimal("0.001"))
        elif kilos_peso is not None:
            kilos = kilos_peso
            gramos = int((kilos * Decimal("1000")).quantize(Decimal("1")))
            if not valor_final and prod.precio_gramo > 0:
                valor_final = (Decimal(gramos) * prod.precio_gramo).quantize(Decimal("1"))
        else:
            gramos = 0
            kilos = Decimal("0")

        if not kilos or kilos <= 0:
            return ResultadoSalidaInventario(
                prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), "",
                cantidad=Decimal("0"), unidad_medida="g", concepto_ticket=prod.nombre
            )

        if prod.stock_kilos <= Decimal("0"):
            return ResultadoSalidaInventario(
                prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), "",
                cantidad=Decimal("0"), unidad_medida="g", concepto_ticket=prod.nombre,
                bloqueado_sin_stock=True,
                motivo_bloqueo=f"🚫 *Venta rechazada:* *{prod.nombre}* está AGOTADO (0 Kg disponibles). No se puede vender ni cobrar."
            )

        if kilos > prod.stock_kilos:
            kilos_disp = f"{prod.stock_kilos:.3f}".rstrip("0").rstrip(".")
            return ResultadoSalidaInventario(
                prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), "",
                cantidad=Decimal("0"), unidad_medida="g", concepto_ticket=prod.nombre,
                bloqueado_sin_stock=True,
                motivo_bloqueo=f"🚫 *Venta rechazada:* Stock insuficiente de *{prod.nombre}*. Solicitaste {kilos:.3f} Kg pero solo quedan {kilos_disp} Kg en inventario."
            )

        with transaction.atomic():
            prod = Producto.objects.select_for_update().get(pk=prod.pk)
            prod.stock_kilos = max(Decimal("0"), prod.stock_kilos - kilos)
            prod.save()

        libras = (Decimal(gramos) / Decimal("500")).quantize(Decimal("0.01"))
        gramos_str = f"{gramos:,.0f}".replace(",", ".")
        kilos_str = f"{kilos:.3f}".rstrip("0").rstrip(".")

        if gramos >= 1000 and gramos % 1000 == 0:
            concepto_ticket = f"{prod.nombre} ({gramos // 1000} Kg)"
        else:
            concepto_ticket = f"{prod.nombre} ({gramos_str} g)"

        info_formateada = (
            f"\n📦 *Inventario Actualizado (Cálculo Exacto):*\n"
            f"   • Producto: *{prod.nombre}*\n"
            f"   • Salida vendida: *{gramos_str} gramos* ({kilos_str} Kg / {libras} lb)\n"
            f"   • Stock restante: *{prod.stock_kilos} Kg* ({prod.stock_libras} lb / {prod.stock_gramos:,} g)"
        )
        if prod.stock_kilos <= Decimal("0"):
            registrar_o_actualizar_pedido(
                establecimiento=establecimiento, nombre_producto=prod.nombre, producto=prod,
                origen="agotado", cantidad_sugerida=Decimal("10.0"), unidad="Kg", observacion="Agotado tras venta reciente"
            )
            info_formateada += f"\n\n🚨 *ALERTA COMPRAR:* ¡Stock de *{prod.nombre}* AGOTADO (0 Kg restantes)!"
        elif prod.stock_kilos <= Decimal("3.0"):
            registrar_o_actualizar_pedido(
                establecimiento=establecimiento, nombre_producto=prod.nombre, producto=prod,
                origen="stock_bajo", cantidad_sugerida=Decimal("5.0"), unidad="Kg", observacion=f"Stock bajo: {prod.stock_kilos} Kg"
            )
            info_formateada += f"\n\n⚠️ *ALERTA COMPRAR:* Stock de *{prod.nombre}* bajo ({prod.stock_kilos} Kg restantes)."

        return ResultadoSalidaInventario(
            prod, kilos, libras, valor_final or Decimal("0"), info_formateada,
            cantidad=Decimal(gramos), unidad_medida="g", concepto_ticket=concepto_ticket
        )

    # -------------------------------------------------------------
    # CASO 2: LÍQUIDOS (Aceite, Bebidas, Leche en Litros / ml)
    # -------------------------------------------------------------
    elif es_liquido or volumen_ml is not None:
        precio_ml = prod.precio_kilo / Decimal("1000") if prod.precio_kilo > 0 else Decimal("0")
        if volumen_ml is None and valor_final and precio_ml > 0:
            ml = int((Decimal(valor_final) / precio_ml).quantize(Decimal("1")))
            litros = (Decimal(ml) / Decimal("1000")).quantize(Decimal("0.001"))
        elif volumen_ml is not None:
            ml = int(volumen_ml[0])
            litros = (Decimal(ml) / Decimal("1000")).quantize(Decimal("0.001"))
            if not valor_final and precio_ml > 0:
                valor_final = (Decimal(ml) * precio_ml).quantize(Decimal("1"))
        else:
            ml = 0
            litros = Decimal("0")

        if not litros or litros <= 0:
            return ResultadoSalidaInventario(
                prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), "",
                cantidad=Decimal("0"), unidad_medida="ml", concepto_ticket=prod.nombre
            )

        if prod.stock_kilos <= Decimal("0"):
            return ResultadoSalidaInventario(
                prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), "",
                cantidad=Decimal("0"), unidad_medida="ml", concepto_ticket=prod.nombre,
                bloqueado_sin_stock=True,
                motivo_bloqueo=f"🚫 *Venta rechazada:* *{prod.nombre}* está AGOTADO (0 Lt disponibles). No se puede vender ni cobrar."
            )

        if litros > prod.stock_kilos:
            litros_disp = f"{prod.stock_kilos:.3f}".rstrip("0").rstrip(".")
            return ResultadoSalidaInventario(
                prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), "",
                cantidad=Decimal("0"), unidad_medida="ml", concepto_ticket=prod.nombre,
                bloqueado_sin_stock=True,
                motivo_bloqueo=f"🚫 *Venta rechazada:* Stock insuficiente de *{prod.nombre}*. Solicitaste {litros:.3f} Lt pero solo quedan {litros_disp} Lt en inventario."
            )

        with transaction.atomic():
            prod = Producto.objects.select_for_update().get(pk=prod.pk)
            prod.stock_kilos = max(Decimal("0"), prod.stock_kilos - litros)
            prod.save()

        ml_str = f"{ml:,.0f}".replace(",", ".")
        litros_str = f"{litros:.3f}".rstrip("0").rstrip(".")

        if ml >= 1000 and ml % 1000 == 0:
            concepto_ticket = f"{prod.nombre} ({ml // 1000} Lt)"
        else:
            concepto_ticket = f"{prod.nombre} ({ml_str} ml)"

        info_formateada = (
            f"\n📦 *Inventario Actualizado (Líquidos):*\n"
            f"   • Producto: *{prod.nombre}*\n"
            f"   • Salida vendida: *{ml_str} ml* ({litros_str} Lt)\n"
            f"   • Stock restante: *{prod.stock_kilos} Lt* ({prod.stock_kilos * 1000:,.0f} ml)"
        )
        if prod.stock_kilos <= Decimal("0"):
            registrar_o_actualizar_pedido(
                establecimiento=establecimiento, nombre_producto=prod.nombre, producto=prod,
                origen="agotado", cantidad_sugerida=Decimal("5.0"), unidad="Lt", observacion="Agotado tras venta reciente"
            )
            info_formateada += f"\n\n🚨 *ALERTA COMPRAR:* ¡Stock de *{prod.nombre}* AGOTADO (0 Lt restantes)!"
        elif prod.stock_kilos <= Decimal("2.0"):
            registrar_o_actualizar_pedido(
                establecimiento=establecimiento, nombre_producto=prod.nombre, producto=prod,
                origen="stock_bajo", cantidad_sugerida=Decimal("5.0"), unidad="Lt", observacion=f"Stock bajo: {prod.stock_kilos} Lt"
            )
            info_formateada += f"\n\n⚠️ *ALERTA COMPRAR:* Stock de *{prod.nombre}* bajo ({prod.stock_kilos} Lt restantes)."

        return ResultadoSalidaInventario(
            prod, litros, Decimal("0"), valor_final or Decimal("0"), info_formateada,
            cantidad=Decimal(ml), unidad_medida="ml", concepto_ticket=concepto_ticket
        )

    # -------------------------------------------------------------
    # CASO 3: UNIDADES (Pan, Cerveza, Huevos, Abarrotes, Latas, etc.)
    # -------------------------------------------------------------
    else:
        if unidades_und is None and valor_final and prod.precio_kilo > 0:
            cant_und = (Decimal(valor_final) / prod.precio_kilo).quantize(Decimal("1"))
            u_nom = "und"
        elif unidades_und is not None:
            cant_und = unidades_und[0]
            u_nom = unidades_und[1]
            if not valor_final and prod.precio_kilo > 0:
                valor_final = (cant_und * prod.precio_kilo).quantize(Decimal("1"))
        else:
            cant_und = Decimal("1")
            u_nom = "und"
            if not valor_final:
                valor_final = prod.precio_kilo

        if cant_und <= 0:
            return ResultadoSalidaInventario(
                prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), "",
                cantidad=Decimal("0"), unidad_medida="und", concepto_ticket=prod.nombre
            )

        if prod.stock_kilos <= Decimal("0"):
            return ResultadoSalidaInventario(
                prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), "",
                cantidad=Decimal("0"), unidad_medida="und", concepto_ticket=prod.nombre,
                bloqueado_sin_stock=True,
                motivo_bloqueo=f"🚫 *Venta rechazada:* *{prod.nombre}* está AGOTADO (0 unidades disponibles). No se puede registrar la venta ni cobrar."
            )

        if cant_und > prod.stock_kilos:
            cant_int = int(cant_und) if cant_und % 1 == 0 else f"{cant_und:.1f}"
            disp = int(prod.stock_kilos) if prod.stock_kilos % 1 == 0 else f"{prod.stock_kilos:.1f}"
            return ResultadoSalidaInventario(
                prod, Decimal("0"), Decimal("0"), valor_final or Decimal("0"), "",
                cantidad=Decimal("0"), unidad_medida="und", concepto_ticket=prod.nombre,
                bloqueado_sin_stock=True,
                motivo_bloqueo=f"🚫 *Venta rechazada:* Stock insuficiente de *{prod.nombre}*. Solicitaste {cant_int} {u_nom} pero solo quedan {disp} disponibles en inventario."
            )

        with transaction.atomic():
            prod = Producto.objects.select_for_update().get(pk=prod.pk)
            prod.stock_kilos = max(Decimal("0"), prod.stock_kilos - cant_und)
            prod.save()

        cant_int = int(cant_und) if cant_und % 1 == 0 else f"{cant_und:.1f}"
        concepto_ticket = f"{prod.nombre} ({cant_int} {u_nom})"

        stock_int = int(prod.stock_kilos) if prod.stock_kilos % 1 == 0 else f"{prod.stock_kilos:.1f}"
        info_formateada = (
            f"\n📦 *Inventario Actualizado (Unidades):*\n"
            f"   • Producto: *{prod.nombre}*\n"
            f"   • Salida vendida: *{cant_int} {u_nom}*\n"
            f"   • Stock restante: *{stock_int} unidades*"
        )
        if prod.stock_kilos <= Decimal("0"):
            registrar_o_actualizar_pedido(
                establecimiento=establecimiento, nombre_producto=prod.nombre, producto=prod,
                origen="agotado", cantidad_sugerida=Decimal("10"), unidad="und", observacion="Agotado tras venta reciente"
            )
            info_formateada += f"\n\n🚨 *ALERTA COMPRAR:* ¡Stock de *{prod.nombre}* AGOTADO (0 unidades restantes)!"
        elif prod.stock_kilos <= Decimal("5.0"):
            registrar_o_actualizar_pedido(
                establecimiento=establecimiento, nombre_producto=prod.nombre, producto=prod,
                origen="stock_bajo", cantidad_sugerida=Decimal("10"), unidad="und", observacion=f"Stock bajo: {stock_int} und"
            )
            info_formateada += f"\n\n⚠️ *ALERTA COMPRAR:* Stock de *{prod.nombre}* bajo ({stock_int} unidades restantes)."

        return ResultadoSalidaInventario(
            prod, cant_und, Decimal("0"), valor_final or Decimal("0"), info_formateada,
            cantidad=cant_und, unidad_medida=u_nom, concepto_ticket=concepto_ticket
        )


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

    if not prod and hasattr(venta, "producto") and venta.producto:
        prod = venta.producto

    if prod:
        cant_reponer = getattr(venta, "cantidad", Decimal("0"))
        if audit and "kilos=" in audit.valor_nuevo:
            m_k = re.search(r"kilos=([\d\.]+)", audit.valor_nuevo)
            if m_k:
                kilos = Decimal(m_k.group(1))

        if prod.es_unidad():
            if not cant_reponer or cant_reponer <= 0:
                und_m = parsear_unidades(venta.concepto)
                cant_reponer = und_m[0] if und_m else (
                    (Decimal(venta.valor) / prod.precio_kilo).quantize(Decimal("1")) if prod.precio_kilo > 0 else Decimal("1")
                )
            prod.stock_kilos += cant_reponer
            prod.save()
            return f"\n🔄 *Inventario Revertido:* Se repusieron +{int(cant_reponer)} unidades a *{prod.nombre}* (Stock: {int(prod.stock_kilos)} unidades)."

        elif prod.es_liquido():
            if not cant_reponer or cant_reponer <= 0:
                vol_m = parsear_volumen(venta.concepto)
                ml_val = vol_m[0] if vol_m else (
                    int(Decimal(venta.valor) / (prod.precio_kilo / Decimal("1000"))) if prod.precio_kilo > 0 else 0
                )
            else:
                ml_val = cant_reponer
            litros = (Decimal(ml_val) / Decimal("1000")).quantize(Decimal("0.001"))
            prod.stock_kilos += litros
            prod.save()
            return f"\n🔄 *Inventario Revertido:* Se repusieron +{int(ml_val):,} ml (+{litros} Lt) a *{prod.nombre}* (Stock: {prod.stock_kilos} Lt)."

        else:
            if not kilos:
                kilos = parsear_peso(venta.concepto)
                if not kilos and prod.precio_gramo > 0 and venta.valor > 0:
                    gramos = int((Decimal(venta.valor) / prod.precio_gramo).quantize(Decimal("1")))
                    kilos = (Decimal(gramos) / Decimal("1000")).quantize(Decimal("0.001"))
                elif not kilos and cant_reponer > 0:
                    kilos = (cant_reponer / Decimal("1000")).quantize(Decimal("0.001"))

            if kilos and kilos > 0:
                prod.stock_kilos += kilos
                prod.save()
                gramos = int(kilos * 1000)
                libras = (Decimal(gramos) / Decimal("500")).quantize(Decimal("0.01"))
                return f"\n🔄 *Inventario Revertido:* Se repusieron +{gramos:,} g (+{kilos} Kg / +{libras} lb) a *{prod.nombre}* (Stock: {prod.stock_kilos} Kg)."

    return ""


def procesar_entrada_inventario(
    establecimiento, texto: str
) -> Tuple[Optional[Producto], Decimal, str]:
    """
    Interpreta el ingreso o reabastecimiento de mercancía desde Telegram.
    Ejemplos:
      - 'Llegaron 20 kilos de tocino'
      - 'Llego 15 kilos pechuga'
      - 'Entrada 30 kilos papa pastusa'
      - 'Surtir 10 kilos arroz'
      - 'Compra 240000 20 kilos tocino Distribuidora'
    Suma el stock al producto, marca el pedido pendiente como comprado si aplica
    y genera la confirmación en Telegram.
    """
    t = normalizar_texto(texto)
    kilos = parsear_peso(t)
    prod = buscar_producto_en_texto(establecimiento, t)

    if not prod:
        termino_limpio = limpiar_termino_consulta(t)
        if termino_limpio:
            prod = buscar_producto_en_texto(establecimiento, termino_limpio)

    if not prod or not kilos or kilos <= 0:
        return None, Decimal("0"), ""

    stock_anterior = prod.stock_kilos
    prod.stock_kilos += kilos
    prod.save()

    # Si estaba en lista de compras pendientes, marcar como comprado/abastecido
    pedidos_resueltos = ItemPedido.objects.filter(
        establecimiento=establecimiento,
        producto=prod,
        estado="pendiente",
    )
    if not pedidos_resueltos.exists():
        pedidos_resueltos = ItemPedido.objects.filter(
            establecimiento=establecimiento,
            nombre_producto__iexact=prod.nombre,
            estado="pendiente",
        )

    cant_resueltos = pedidos_resueltos.count()
    if cant_resueltos > 0:
        pedidos_resueltos.update(estado="comprado")

    gramos = int(kilos * 1000)
    libras = (Decimal(gramos) / Decimal("500")).quantize(Decimal("0.01"))
    gramos_str = f"{gramos:,.0f}".replace(",", ".")
    kilos_str = f"{kilos:.3f}".rstrip("0").rstrip(".")

    info_pedido = ""
    if cant_resueltos > 0:
        info_pedido = "\n   ✅ *Lista de compras actualizada:* Producto marcado como *ABASTECIDO* en `/pedidos`."

    info = (
        "📦 *¡Entrada de Mercancía Registrada con Éxito!*\n"
        f"📍 _{establecimiento.nombre}_\n\n"
        f"   • Producto: *{prod.nombre}*\n"
        f"   • Cantidad ingresada: *+{gramos_str} g* (+{kilos_str} Kg / +{libras} lb)\n"
        f"   • Stock anterior: {stock_anterior:,.2f} Kg\n"
        f"   • *Nuevo stock disponible:* *{prod.stock_kilos:,.2f} Kg* ({prod.stock_libras} lb / {prod.stock_gramos:,} g)"
        f"{info_pedido}"
    )

    return prod, kilos, info


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


def limpiar_termino_consulta(texto: str) -> Optional[str]:
    """
    Extrae únicamente el nombre del producto de una pregunta o frase,
    removiendo saludos, verbos y prefijos cotidianos como:
      'tienes pan' -> 'pan'
      'tiene salchichas' -> 'salchichas'
      'hay queso' -> 'queso'
      'tienen huevos?' -> 'huevos'
      'cuanto vale el arroz' -> 'arroz'
      'a como tiene el tomate' -> 'tomate'
      'buenas vecino tiene panela' -> 'panela'
      'tienes' -> None (no es producto)
      'hay' -> None (no es producto)
    """
    t = normalizar_texto(texto)
    t = re.sub(r"[¿\?\.!,;:_]", " ", t)

    # 1. Quitar saludos y fórmulas de cortesía
    t = re.sub(
        r"\b(hola|buenas|buenos dias|buenas tardes|buenas noches|vecino|vecina|amigo|amiga|don|dona|por favor|favor)\b",
        " ",
        t,
    )

    # 2. Quitar verbos y fórmulas de pregunta o disponibilidad
    patrones_verbos = [
        r"\b(?:cuanto\s+(?:vale|cuesta)|a\s+como(?:\s+esta|\s+sale)?|precio(?:\s+de|\s+del)?)\b",
        r"\b(?:me\s+vende|me\s+da|deme|regalame|vengo\s+por|busco|quiero|necesito)\b",
        r"\b(?:tienes|tiene|tenes|tienen|tenemos|tengo)\b",
        r"\b(?:hay|habra|habria|queda|quedan|quedo)\b",
        r"\b(?:vendes|vende|venden|vender)\b",
        r"\b(?:consigue|consigues|consiguen|trae|traes|trajeron)\b",
        r"\b(?:disponible|disponibles)\b",
    ]
    for patron in patrones_verbos:
        t = re.sub(patron, " ", t)

    # 3. Quitar artículos, preposiciones y cuantificadores vagos al inicio o aislados
    t = re.sub(r"\b(?:de|del|el|la|los|las|un|una|unos|unas|algun|alguna|algunos|algunas|para)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()

    # 4. Lista negra de palabras que jamás son un producto
    palabras_invalidas = {
        "tienes", "tiene", "tenes", "tienen", "tenemos", "tengo", "hay", "queda",
        "quedan", "vendes", "vende", "venden", "cuanto", "vale", "cuesta", "precio",
        "algo", "que", "para", "por", "favor", "buenas", "hola", "don", "dona", "vecino"
    }

    if not t or t in palabras_invalidas or len(t) < 2:
        return None

    return t


def consultar_producto_o_categoria(establecimiento, texto: str) -> Optional[str]:
    """
    Detecta si el mensaje es una pregunta en lenguaje natural sobre inventario:
      - 'que carnes tengo', 'que abarrotes hay', 'que lacteos hay', 'que verduras hay'
      - 'tienes pan', 'tienes salchicha', 'hay queso campesino', 'tenemos tocino'
      - 'cuanto vale el arroz', 'cuanto cuesta la pechuga', 'precio del tocino'
    Retorna la respuesta formateada o None si no corresponde a una consulta de inventario.
    """
    t = normalizar_texto(texto).strip()
    if texto.strip().startswith("/") or "/" in t:
        return None
    t_limpio = re.sub(r"[¿\?\.!]", "", t).strip()

    # 1. Preguntas de categorías completas: "que carnes tengo", "que abarrotes hay"
    m_cat = re.search(
        r"^(?:que|cuales|ver|mostrar|lista de)\s+(carnes?|abarrotes|viveres|granos|lacteos|huevos|frutas?|verduras?|fruver|bebidas?|aseo)(?:\s+(?:hay|tengo|tenemos|vendes|venden))?$",
        t_limpio,
    )
    if m_cat:
        cat_pedida = m_cat.group(1)
        return generar_lista_categoria(establecimiento, cat_pedida)

    # 2. Si solo escribió "tienes", "tiene", "hay" sin nombrar producto
    if t_limpio in ("tienes", "tiene", "tenes", "tienen", "tenemos", "hay", "que tienes", "que tiene", "que hay", "buenas tienes"):
        return (
            "💬 *¿Qué producto estás buscando?*\n\n"
            "Puedes preguntarme por ejemplo:\n"
            "• _¿Tienes pan?_\n"
            "• _¿Hay salchicha?_\n"
            "• _¿Cuánto vale el queso campesino?_\n"
            "• _¿Tenemos tocino?_\n\n"
            "O escribe `/inventario` para ver los departamentos disponibles."
        )

    # 3. No procesar si es un comando contable o de venta explícita
    comandos_excluidos = (
        "venta", "vendi", "compra", "retencion", "anular", "resumen",
        "consolidado", "ayuda", "historial", "start", "hoy", "caja",
        "comprar", "pedido", "pedidos", "faltantes"
    )
    if any(k in t_limpio for k in comandos_excluidos):
        return None

    # Si contiene dígitos (números de dinero o peso), dejarlo pasar para registro de venta
    if any(char.isdigit() for char in t_limpio):
        return None

    # 4. Extraer el nombre limpio del producto sin 'tienes', 'hay', artículos, etc.
    termino_busqueda = limpiar_termino_consulta(t_limpio)
    if not termino_busqueda:
        return None

    # Si preguntó genéricamente "carne" o "abarrotes", mostrar categoría
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

    # Buscar producto específico en catálogo
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
    nombre_limpio = termino_busqueda.strip().capitalize()

    if len(nombre_limpio) >= 2:
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
        n_norm = normalizar_texto(nombre_limpio)
        for p in pendientes:
            p_norm = normalizar_texto(p.nombre_producto)
            if p_norm == n_norm or p_norm == n_norm.rstrip("s") or n_norm == p_norm.rstrip("s"):
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

