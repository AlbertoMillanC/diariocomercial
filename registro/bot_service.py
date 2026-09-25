"""
registro/bot_service.py
Motor central agnóstico de mensajería (Telegram / WhatsApp / Mostrador)
con resolución Multi-Tenant estricta, RBAC (Propietario vs Dependiente),
idempotencia anti-duplicados y modo Carrito/Ticket Abierto.
"""
import re
import secrets
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .models import (
    Establecimiento,
    Perfil,
    ActividadCIIU,
    Venta,
    Compra,
    Producto,
    ItemPedido,
    VinculoCanal,
    TokenVinculacion,
    MensajeProcesado,
    Cliente,
)
from .inventario_service import (
    normalizar_texto,
    parsear_dinero,
    parsear_peso,
    procesar_salida_inventario,
    procesar_entrada_inventario,
    buscar_producto_en_texto,
)


# Buffer en memoria para tickets abiertos / carritos por canal e identificador
# Estructura: {(canal, id_externo): {"items": [], "iniciado": datetime}}
_TICKETS_ABIERTOS: Dict[Tuple[str, str], Dict[str, Any]] = {}


def generar_token_vinculacion(usuario: User, establecimiento: Establecimiento, duracion_minutos: int = 60) -> str:
    """Genera un token de 6 dígitos seguro para vincular un dispositivo en 1 toque."""
    pin_6 = f"{secrets.randbelow(900000) + 100000}"
    token = f"auth_{pin_6}"
    expira = timezone.now() + timezone.timedelta(minutes=duracion_minutos)
    TokenVinculacion.objects.create(
        token=token,
        usuario=usuario,
        establecimiento=establecimiento,
        expira=expira,
    )
    return token


def despachar_mensaje(
    canal: str,
    identificador_externo: str,
    texto_mensaje: str,
    identificador_mensaje: Optional[str] = None,
    username_externo: str = "",
    nombre_remitente: str = "",
    return_adjuntos: bool = False,
) -> Any:
    """
    Punto de entrada único agnóstico para Telegram, WhatsApp o simuladores.
    Aplica:
      1. Idempotencia anti-reintentos de red.
      2. Resolución de Multi-Tenancy.
      3. Procesamiento de Tokens de Vinculación.
      4. Control de Acceso basado en Roles (RBAC).
      5. Parsing lingüístico colombiano y modo Carrito.
    """
    canal = canal.lower().strip()
    identificador_externo = str(identificador_externo).strip()
    texto = (texto_mensaje or "").strip()

    # 1. Filtro de Idempotencia si viene identificador_mensaje
    if identificador_mensaje:
        id_msg_str = f"{canal}_{identificador_mensaje}"
        procesado = MensajeProcesado.objects.filter(identificador_mensaje=id_msg_str).first()
        if procesado and procesado.respuesta_cacheada:
            return procesado.respuesta_cacheada

    # 2. Resolución del Vínculo Multi-Tenant
    vinculo = (
        VinculoCanal.objects.select_related("usuario", "establecimiento")
        .filter(canal=canal, identificador_externo=identificador_externo, activo=True)
        .first()
    )

    # 3. Flujo de Vinculación por Token (/start auth_XYZ o código de 6 dígitos)
    match_token = re.search(r"auth_[A-Za-z0-9_-]+", texto)
    if not match_token:
        match_pin = re.search(r"\b\d{6}\b", texto)
        token_str = f"auth_{match_pin.group(0)}" if match_pin else None
    else:
        token_str = match_token.group(0)

    if token_str:
        tok = TokenVinculacion.objects.select_related("usuario", "establecimiento").filter(token=token_str).first()
        if tok and tok.es_valido():
            tok.usado = True
            tok.save()

            vinculo, _ = VinculoCanal.objects.update_or_create(
                canal=canal,
                identificador_externo=identificador_externo,
                defaults={
                    "usuario": tok.usuario,
                    "establecimiento": tok.establecimiento,
                    "username_externo": username_externo,
                    "nombre_remitente": nombre_remitente,
                    "activo": True,
                },
            )
            resp = (
                f"🎉 *¡Dispositivo Vinculado Exitosamente!*\n\n"
                f"📍 *Establecimiento:* {tok.establecimiento.nombre}\n"
                f"👤 *Usuario:* {tok.usuario.username}\n\n"
                f"Ya puedes registrar ventas, compras y consultar existencias directamente desde aquí.\n"
                f"Escribe un valor o producto (ej: `40 mil carne molida nequi`) para probar."
            )
            _cachear_respuesta(canal, identificador_mensaje, resp)
            return (resp, None, None) if return_adjuntos else resp
        elif tok and tok.usado:
            resp = "⚠️ Este código de vinculación ya fue utilizado previamente."
            _cachear_respuesta(canal, identificador_mensaje, resp)
            return (resp, None, None) if return_adjuntos else resp
        else:
            resp = "❌ Código de vinculación inválido o expirado. Genera uno nuevo en el panel de Configuración."
            _cachear_respuesta(canal, identificador_mensaje, resp)
            return (resp, None, None) if return_adjuntos else resp

    # Si no está vinculado, rechazo amable con instrucciones
    if not vinculo:
        resp = (
            "👋 *¡Hola! Bienvenido a DiarioComercial.*\n\n"
            "Tu cuenta aún no está vinculada a ningún comercio.\n\n"
            "👉 Para conectar este canal en 1 toque:\n"
            "1. Inicia sesión en la plataforma web de tu negocio.\n"
            "2. Ve a *Configuración* ➔ *Asistente Móvil*.\n"
            "3. Pulsa el botón *Conectar Telegram / WhatsApp*.\n\n"
            "_(Si tienes un código de acceso, envíalo directamente aquí)_."
        )
        _cachear_respuesta(canal, identificador_mensaje, resp)
        return (resp, None, None) if return_adjuntos else resp

    # Identificar Rol del Usuario
    establecimiento = vinculo.establecimiento
    usuario = vinculo.usuario
    perfil = Perfil.objects.filter(user=usuario, establecimiento=establecimiento).first()
    es_propietario = perfil.es_propietario() if perfil else False

    # 4. Enrutamiento del Comando
    t_norm = normalizar_texto(texto)

    # 4.1 Comandos Restringidos (Solo Propietario)
    if any(t_norm.startswith(cmd) for cmd in ["/hoy", "cierre de caja", "arqueo", "cierre", "/excel", "/consolidado", "/resumen", "/anular"]):
        if not es_propietario:
            resp = (
                "⛔ *Acceso Restringido*\n\n"
                "El arqueo general de caja, los reportes contables y la anulación de transacciones "
                "están reservados exclusivamente al propietario del negocio."
            )
            _cachear_respuesta(canal, identificador_mensaje, resp)
            return (resp, None, None) if return_adjuntos else resp

    # Ejecución de Arqueo / Cierre
    if t_norm.startswith(("/hoy", "cierre de caja", "arqueo", "cierre")):
        resp = _generar_arqueo_hoy(establecimiento)
        _cachear_respuesta(canal, identificador_mensaje, resp)
        return (resp, None, None) if return_adjuntos else resp

    # Ayuda y Comandos
    if t_norm.startswith(("/ayuda", "/comandos", "/guia", "ayuda", "comandos")):
        resp = _generar_ayuda(es_propietario)
        _cachear_respuesta(canal, identificador_mensaje, resp)
        return (resp, None, None) if return_adjuntos else resp

    # Consulta de Stock / Precios
    if t_norm.startswith(("/stock", "/precio", "stock", "precio", "cuanto queda", "hay")):
        resp = _consultar_stock(establecimiento, texto)
        _cachear_respuesta(canal, identificador_mensaje, resp)
        return (resp, None, None) if return_adjuntos else resp

    # Registro de Compra / Gasto
    if t_norm.startswith(("/compra", "compra", "gasto", "factura compra")):
        resp = _registrar_compra(establecimiento, usuario, texto)
        _cachear_respuesta(canal, identificador_mensaje, resp)
        return (resp, None, None) if return_adjuntos else resp

    # 4.2 Cobro Rápido con Código QR Bre-B (Estilo WeChat Pay)
    if t_norm.startswith(("/cobrar", "cobrar", "/qr", "qr", "generar qr", "cobro qr")):
        val, _ = parsear_dinero(texto)
        if not val or val <= 0:
            resp = (
                "❓ Indique el monto para generar el código QR Bre-B.\n"
                "Ejemplo: `/cobrar 45 mil` o `/qr 20000 pechuga`."
            )
            _cachear_respuesta(canal, identificador_mensaje, resp)
            return (resp, None, None) if return_adjuntos else resp

        from .bre_b_service import generar_qr_dinamico_bre_b
        tx, payload = generar_qr_dinamico_bre_b(
            establecimiento=establecimiento,
            monto=val,
            comando_original=texto,
        )
        valor_fmt = f"${val:,.0f} COP".replace(",", ".")
        resp = (
            f"⚡ *Cobro Bre-B Generado (WeChat Pay):* {valor_fmt}\n"
            f"Ref: `{tx.referencia_unica}` • Token: `{tx.token_visual_corto}`\n\n"
            f"📲 Muestre el siguiente código QR al cliente en su mostrador para pagar en 2 segundos desde *Nequi, Daviplata, Bancolombia* o cualquier banco."
        )
        _cachear_respuesta(canal, identificador_mensaje, resp)
        cobro_info = {"monto": val, "tx": tx, "payload": payload, "establecimiento": establecimiento}
        return (resp, None, cobro_info) if return_adjuntos else resp

    # 4.3 Registro de Venta (Flujo Natural del Mostrador)
    resp, venta_creada = _registrar_venta(establecimiento, usuario, texto)
    _cachear_respuesta(canal, identificador_mensaje, resp)
    return (resp, venta_creada, None) if return_adjuntos else resp


def _cachear_respuesta(canal: str, id_msg: Optional[str], respuesta: str):
    if id_msg:
        MensajeProcesado.objects.create(
            canal=canal,
            identificador_mensaje=f"{canal}_{id_msg}",
            respuesta_cacheada=respuesta,
        )


def _registrar_venta(establecimiento: Establecimiento, usuario: User, texto: str) -> str:
    """Interpreta y asienta la venta con descuento atómico de inventario e ICA."""
    val, texto_sin_dinero = parsear_dinero(texto)
    kilos = parsear_peso(texto)

    # Detección de medio de pago
    t_lower = normalizar_texto(texto)
    medio_pago = "efectivo"
    badge_pago = "💵 Efectivo"
    if "nequi" in t_lower:
        medio_pago = "nequi"
        badge_pago = "📱 Nequi"
    elif "daviplata" in t_lower:
        medio_pago = "daviplata"
        badge_pago = "📲 Daviplata"
    elif "bre-b" in t_lower or "breb" in t_lower or "bre b" in t_lower:
        medio_pago = "bre_b"
        badge_pago = "⚡ Bre-B"
    elif any(w in t_lower for w in ["transferencia", "bancolombia", "transfiya"]):
        medio_pago = "transferencia"
        badge_pago = "🏦 Transferencia"

    # Procesar inventario
    prod, kg_desc, lb_desc, val_final, info_inv = procesar_salida_inventario(
        establecimiento, texto, valor_ingresado=val
    )

    if not val_final or val_final <= 0:
        return (
            "❓ No entendí el valor de la venta.\n"
            "Ejemplo: `40 mil carne molida nequi` o `2 libras pechuga`.",
            None,
        )

    # Actividad principal para ICA
    actividad = establecimiento.actividades.first()

    with transaction.atomic():
        venta = Venta.objects.create(
            establecimiento=establecimiento,
            usuario=usuario,
            actividad=actividad,
            fecha=timezone.localdate(),
            fecha_hora=timezone.now(),
            valor=val_final,
            concepto=prod.nombre if prod else (texto_sin_dinero[:120] or "Venta general"),
            medio_pago=medio_pago,
            estado="vigente",
        )

    valor_fmt = f"${val_final:,.0f}".replace(",", ".")
    concepto_fmt = prod.nombre if prod else "Venta general"
    stock_fmt = f" | Quedan {prod.stock_kilos} Kg" if prod else ""

    resp = (
        f"✅ *Venta Registrada:* {valor_fmt} ({concepto_fmt}) {badge_pago}{stock_fmt}\n"
        f"🧾 Ticket #{venta.pk} | {timezone.localtime(venta.fecha_hora).strftime('%I:%M %p')}"
    )
    if info_inv:
        resp += f"\n{info_inv}"

    return resp, venta


def _registrar_compra(establecimiento: Establecimiento, usuario: User, texto: str) -> str:
    """Registra una compra o gasto."""
    val, resto = parsear_dinero(texto)
    if not val or val <= 0:
        return "❓ No identifiqué el valor de la compra. Ejemplo: `compra 150 mil proveedor carnes`."

    # Procesar entrada a inventario si corresponde
    prod, kg_inc, info_inv = procesar_entrada_inventario(establecimiento, texto)

    with transaction.atomic():
        compra = Compra.objects.create(
            establecimiento=establecimiento,
            usuario=usuario,
            fecha=timezone.localdate(),
            valor=val,
            proveedor=resto[:100] or "Proveedor local",
            concepto=f"Entrada stock: {prod.nombre}" if prod else resto[:150],
            estado="vigente",
        )

    valor_fmt = f"${val:,.0f}".replace(",", ".")
    return f"📥 *Compra Registrada:* {valor_fmt} a {compra.proveedor}\nRef #{compra.pk}"


def _consultar_stock(establecimiento: Establecimiento, texto: str) -> str:
    """Consulta existencias de uno o varios productos."""
    prod = buscar_producto_en_texto(establecimiento, texto)
    if prod:
        return (
            f"📦 *Existencias de {prod.nombre}:*\n"
            f"   • Stock: *{prod.stock_kilos} Kg* ({prod.stock_libras} lb / {prod.stock_gramos:,} g)\n"
            f"   • Precio: *${prod.precio_kilo:,.0f}/Kg* (${prod.precio_libra:,.0f}/lb)"
        )

    # Si no especificó producto, mostrar los 5 productos con menos stock
    bajos = Producto.objects.filter(establecimiento=establecimiento, estado="activo").order_by("stock_kilos")[:5]
    if not bajos:
        return "📦 No hay productos registrados en el inventario de este establecimiento."

    items_txt = "\n".join([f"• *{p.nombre}*: {p.stock_kilos} Kg (${p.precio_kilo:,.0f}/Kg)" for p in bajos])
    return f"📦 *Productos con menor stock:*\n{items_txt}"


def _generar_arqueo_hoy(establecimiento: Establecimiento) -> str:
    """Genera el cierre de caja del día para el propietario."""
    hoy = timezone.localdate()
    ventas = Venta.objects.filter(establecimiento=establecimiento, fecha=hoy, estado="vigente")
    compras = Compra.objects.filter(establecimiento=establecimiento, fecha=hoy, estado="vigente")

    tot_ventas = sum([v.valor for v in ventas], Decimal("0"))
    tot_compras = sum([c.valor for c in compras], Decimal("0"))
    tot_ica = sum([v.ica_estimado for v in ventas], Decimal("0"))

    # Desglose por medio de pago
    efectivo = sum([v.valor for v in ventas if v.medio_pago == "efectivo"], Decimal("0"))
    nequi = sum([v.valor for v in ventas if v.medio_pago == "nequi"], Decimal("0"))
    daviplata = sum([v.valor for v in ventas if v.medio_pago == "daviplata"], Decimal("0"))
    bre_b = sum([v.valor for v in ventas if v.medio_pago == "bre_b"], Decimal("0"))
    transferencia = sum([v.valor for v in ventas if v.medio_pago == "transferencia"], Decimal("0"))

    caja_neta = tot_ventas - tot_compras

    return (
        f"☀️ *Cierre de Caja - {establecimiento.nombre}*\n"
        f"📅 Fecha: {hoy.strftime('%d/%m/%Y')}\n"
        f"────────────────────────\n"
        f"🧾 *Operaciones:* {ventas.count()} ventas | {compras.count()} compras\n"
        f"💵 *Total Ventas:* ${tot_ventas:,.0f} COP\n"
        f"📉 *Total Gastos/Compras:* ${tot_compras:,.0f} COP\n"
        f"💰 *Caja Neta del Día:* ${caja_neta:,.0f} COP\n\n"
        f"💳 *Desglose por Medio de Pago:*\n"
        f"   • 💵 Efectivo: ${efectivo:,.0f}\n"
        f"   • 📱 Nequi: ${nequi:,.0f}\n"
        f"   • 📲 Daviplata: ${daviplata:,.0f}\n"
        f"   • ⚡ Bre-B: ${bre_b:,.0f}\n"
        f"   • 🏦 Transferencias: ${transferencia:,.0f}\n\n"
        f"🏛️ *ICA Estimado del Día:* ${tot_ica:,.0f} COP"
    )


def _generar_ayuda(es_propietario: bool) -> str:
    ayuda = (
        "🤖 *Comandos y Uso de DiarioComercial:*\n\n"
        "• *Registrar Venta:* `40 mil carne molida nequi` o `2 libras pechuga`\n"
        "• *Registrar Compra:* `compra 80 mil proveedor verduras`\n"
        "• *Consultar Stock:* `cuanto queda de costilla` o `/stock`\n"
    )
    if es_propietario:
        ayuda += (
            "\n👑 *Comandos de Propietario:*\n"
            "• `/hoy` o `cierre`: Arqueo y balance discriminado de caja\n"
        )
    return ayuda
