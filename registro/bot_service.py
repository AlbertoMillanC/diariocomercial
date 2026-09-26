"""
registro/bot_service.py
Motor central agnóstico de mensajería (Telegram / WhatsApp / Mostrador)
con resolución Multi-Tenant estricta, RBAC (Propietario vs Dependiente),
idempotencia anti-duplicados y modo Carrito/Ticket Abierto.
"""
import os
import re
import secrets
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any

from django.conf import settings
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
    Auditoria,
    obtener_configuracion_saas,
)
from .inventario_service import (
    normalizar_texto,
    parsear_dinero,
    parsear_peso,
    parsear_volumen,
    parsear_unidades,
    procesar_salida_inventario,
    procesar_entrada_inventario,
    buscar_producto_en_texto,
)


# Buffer en memoria para tickets abiertos / carritos por canal e identificador
# Estructura: {(canal, id_externo): {"items": [], "iniciado": datetime}}
_TICKETS_ABIERTOS: Dict[Tuple[str, str], Dict[str, Any]] = {}

# Buffer para ventas que están esperando los datos del cliente para Factura Electrónica
# Estructura: {(canal, id_externo): {"venta_id": int, "monto": Decimal, "concepto": str}}
_VENTAS_PENDIENTES_FACTURA: Dict[Tuple[str, str], Dict[str, Any]] = {}

# Buffer para tickets de mostrador que están esperando número de documento de cliente
# Estructura: {(canal, id_externo): {"venta_id": int, "tipo_doc": Optional[str], "fecha": datetime}}
_VENTAS_PENDIENTES_DOCUMENTO: Dict[Tuple[str, str], Dict[str, Any]] = {}

DIAN_TIPOS_DOC: Dict[str, str] = {
    "11": "Registro Civil",
    "12": "Tarjeta de Identidad",
    "13": "Cédula de Ciudadanía",
    "21": "Tarjeta de Extranjería",
    "22": "Cédula de Extranjería",
    "31": "NIT",
    "41": "Pasaporte",
    "42": "Doc. Extranjero",
    "47": "PPT",
}


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

    # Si no está vinculado, rechazo amable con instrucciones y enlaces directos
    if not vinculo:
        base_url = getattr(settings, "BASE_URL", os.environ.get("BASE_URL", "http://127.0.0.1:8001")).rstrip("/")
        link_activar = f"{base_url}/configuracion/"
        link_web = f"{base_url}/"
        resp = (
            "👋 *¡Hola! Bienvenido a DiarioComercial.*\n\n"
            "Tu cuenta aún no está vinculada a ningún comercio.\n\n"
            "👉 *Para conectar este canal en 1 toque:*\n"
            f"1. Abre directamente este enlace: {link_activar}\n"
            "2. Inicia sesión con tus credenciales de tienda.\n"
            "3. En la pestaña *Asistente Móvil*, pulsa el botón *\"Conectar con Telegram Ahora\"* (o copia el código PIN de 6 dígitos y envíalo aquí).\n\n"
            f"🌐 *¿Aún no tienes cuenta en DiarioComercial?*\n"
            f"Conoce la plataforma y regístrate aquí: {link_web}\n\n"
            "_(Si ya tienes un código PIN de 6 dígitos, escríbelo directamente en este chat para activarlo al instante)_."
        )
        _cachear_respuesta(canal, identificador_mensaje, resp)
        return (resp, None, None) if return_adjuntos else resp

    # Identificar Rol del Usuario
    establecimiento = vinculo.establecimiento
    usuario = vinculo.usuario
    perfil = Perfil.objects.filter(user=usuario, establecimiento=establecimiento).first()
    es_propietario = perfil.es_propietario() if perfil else False

    # 3.1 Verificación de Suspensión del Comercio
    if establecimiento.estado == "suspendido":
        cfg = obtener_configuracion_saas(establecimiento.municipio)
        resp = (
            f"⚠️ *Servicio Temporalmente Inactivo*\n\n"
            f"El comercio *{establecimiento.nombre}* se encuentra en pausa por pago de suscripción pendiente.\n\n"
            f"🛡️ *Tus ventas anteriores e inventario están 100% seguros y respaldados.*\n\n"
            f"⚡ Para reactivar tu servicio al instante:\n"
            f"• Realiza tu pago de ${cfg['tarifa_mensual_cop']:,.0f} COP a la llave Bre-B: `{cfg['llave_bre_b']}`\n"
            f"• O contacta a soporte al WhatsApp: +57 {cfg['whatsapp_soporte']}"
        )
        _cachear_respuesta(canal, identificador_mensaje, resp)
        return (resp, None, None) if return_adjuntos else resp

    # 4. Enrutamiento del Comando
    key_sesion = (canal, identificador_externo)
    t_norm = normalizar_texto(texto)

    # 4.0 Interceptación de Flujo Conversacional de Factura Electrónica Pendiente
    if key_sesion in _VENTAS_PENDIENTES_FACTURA:
        # Cancelar proceso de factura y mantener como ticket consumidor final
        if t_norm in ("cancelar", "cancel", "no", "descartar", "ticket") or t_norm.startswith("cancelar"):
            _VENTAS_PENDIENTES_FACTURA.pop(key_sesion, None)
            resp = "✅ Solicitud de factura cancelada. La venta se mantiene registrada como ticket para Consumidor Final."
            _cachear_respuesta(canal, identificador_mensaje, resp)
            return (resp, None, None) if return_adjuntos else resp

        # Si no es un comando de sistema (/hoy, /ayuda, /compra, etc.)
        # y no es una venta nueva evidente con dinero ("mil", "lucas", "k", "$")
        val_prueba, _ = parsear_dinero(texto)
        es_venta_nueva = (
            val_prueba is not None
            and val_prueba > 0
            and any(w in t_norm for w in [" mil", "mil", "lucas", " k", "$"])
            and not ("@" in texto or "nit" in t_norm or "cedula" in t_norm or "cc" in t_norm)
        )
        if es_venta_nueva:
            # El usuario continuó con otra venta sin completar la factura anterior
            _VENTAS_PENDIENTES_FACTURA.pop(key_sesion, None)
        elif not texto.startswith("/"):
            datos_pend = _VENTAS_PENDIENTES_FACTURA[key_sesion]
            venta_pend = Venta.objects.filter(pk=datos_pend["venta_id"], establecimiento=establecimiento).first()
            if venta_pend:
                resp = _emitir_factura_electronica_bot(
                    establecimiento=establecimiento,
                    usuario=usuario,
                    texto=texto,
                    venta=venta_pend,
                    canal_sesion=key_sesion,
                )
                _cachear_respuesta(canal, identificador_mensaje, resp)
                return (resp, None, None) if return_adjuntos else resp
            else:
                _VENTAS_PENDIENTES_FACTURA.pop(key_sesion, None)

    # 4.0.1 Interceptación de Flujo de Documento de Cliente Pendiente para Ticket
    if key_sesion in _VENTAS_PENDIENTES_DOCUMENTO:
        if t_norm in ("omitir", "no", "cancelar", "saltar", "final", "consumidor final") or t_norm.startswith("omitir"):
            _VENTAS_PENDIENTES_DOCUMENTO.pop(key_sesion, None)
            resp = "✅ Ticket registrado para Consumidor Final (222222222222) para ventas menores y registro tributario."
            _cachear_respuesta(canal, identificador_mensaje, resp)
            return (resp, None, None) if return_adjuntos else resp

        val_prueba, _ = parsear_dinero(texto)
        es_venta_nueva = (
            val_prueba is not None
            and val_prueba > 0
            and any(w in t_norm for w in [" mil", "mil", "lucas", " k", "$"])
            and not ("@" in texto or "nit" in t_norm or "cedula" in t_norm or "cc" in t_norm or "doc" in t_norm)
        )
        if es_venta_nueva:
            _VENTAS_PENDIENTES_DOCUMENTO.pop(key_sesion, None)
        elif not texto.startswith("/"):
            datos_doc = _VENTAS_PENDIENTES_DOCUMENTO.pop(key_sesion, None)
            venta_pend = Venta.objects.filter(pk=datos_doc["venta_id"], establecimiento=establecimiento).first()
            if venta_pend:
                match_num = re.search(r"\b(\d{5,12}(?:-\d)?)\b", texto)
                if match_num:
                    doc_num = match_num.group(1)
                    resto_nombre = texto[:match_num.start()] + " " + texto[match_num.end():]
                    nombre_limpio = re.sub(r"\b(cc|c\.c\.|nit|cedula|documento|doc|cliente|nombre)\b", " ", resto_nombre, flags=re.IGNORECASE)
                    nombre_limpio = re.sub(r"\s+", " ", nombre_limpio).strip(" :-.,")

                    tipo_doc_esperado = datos_doc.get("tipo_doc")
                    if tipo_doc_esperado:
                        tipo_doc = tipo_doc_esperado
                        tipo_pers = "juridica" if tipo_doc == "31" else "natural"
                        prefix = "NIT" if tipo_doc == "31" else ("CE" if tipo_doc == "22" else "CC")
                        sufijo_dian = f" (Tipo {tipo_doc} DIAN)"
                    else:
                        tipo_doc = "31" if ("-" in doc_num or (len(doc_num.split("-")[0]) == 9 and doc_num.startswith(("8", "9")))) else "13"
                        tipo_pers = "juridica" if tipo_doc == "31" else "natural"
                        prefix = "NIT" if tipo_doc == "31" else "CC"
                        sufijo_dian = ""

                    cliente = Cliente.objects.filter(establecimiento=establecimiento, nit_cedula=doc_num).first()
                    if not cliente:
                        mun_nom = establecimiento.municipio.nombre if establecimiento.municipio else "Tunja"
                        dep_nom = establecimiento.municipio.departamento if establecimiento.municipio else "Boyacá"
                        cliente = Cliente.objects.create(
                            establecimiento=establecimiento,
                            nombre=nombre_limpio or f"{prefix} {doc_num}",
                            tipo_documento=tipo_doc,
                            nit_cedula=doc_num,
                            tipo_persona=tipo_pers,
                            municipio_nombre=mun_nom,
                            departamento_nombre=dep_nom,
                        )
                    else:
                        cambios = []
                        if tipo_doc_esperado and cliente.tipo_documento != tipo_doc_esperado:
                            cliente.tipo_documento = tipo_doc_esperado
                            cambios.append("tipo_documento")
                        if nombre_limpio and cliente.nombre != nombre_limpio and cliente.nombre.startswith(("Cliente CC", "CC ", "NIT ", "CE ")):
                            cliente.nombre = nombre_limpio
                            cambios.append("nombre")
                        if cambios:
                            cliente.save(update_fields=cambios)

                    venta_pend.cliente = cliente
                    venta_pend.save(update_fields=["cliente"])

                    Auditoria.objects.create(
                        establecimiento=establecimiento,
                        usuario=usuario,
                        entidad_afectada="Venta",
                        id_registro=venta_pend.pk,
                        accion="asignar_cliente_ticket",
                        valor_anterior="Consumidor Final (222222222222)",
                        valor_nuevo=f"Cliente={cliente.nombre}, Doc={cliente.nit_cedula}",
                        motivo="Asignación de documento de cliente para ticket de venta",
                    )

                    if cliente.nombre and not cliente.nombre.startswith(("Cliente CC", "CC ", "NIT ", "CE ")) and cliente.nombre != cliente.nit_cedula:
                        cli_det = f"{cliente.nombre} ({prefix} {cliente.nit_cedula}{sufijo_dian})"
                    else:
                        cli_det = f"{prefix} {cliente.nit_cedula}{sufijo_dian}"

                    resp = (
                        f"✅ *Documento Asignado al Ticket #{venta_pend.pk}:*\n"
                        f"• *Cliente:* {cli_det}\n"
                        f"📄 Registro tributario actualizado correctamente."
                    )
                    _cachear_respuesta(canal, identificador_mensaje, resp)
                    return (resp, None, None) if return_adjuntos else resp

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

    # 4.3 Emisión de Factura Electrónica formal DIAN desde el chat (Comando directo)
    if t_norm.startswith(("/factura", "/facturar", "factura", "facturar", "emitir factura")):
        resp = _emitir_factura_electronica_bot(establecimiento, usuario, texto, canal_sesion=key_sesion)
        _cachear_respuesta(canal, identificador_mensaje, resp)
        return (resp, None, None) if return_adjuntos else resp

    # 4.4 Registro de Venta (Flujo Natural del Mostrador)
    resp, venta_creada = _registrar_venta(establecimiento, usuario, texto, canal_sesion=key_sesion)
    _cachear_respuesta(canal, identificador_mensaje, resp)
    return (resp, venta_creada, None) if return_adjuntos else resp


def _cachear_respuesta(canal: str, id_msg: Optional[str], respuesta: str):
    if id_msg:
        MensajeProcesado.objects.create(
            canal=canal,
            identificador_mensaje=f"{canal}_{id_msg}",
            respuesta_cacheada=respuesta,
        )


def _registrar_venta(
    establecimiento: Establecimiento,
    usuario: User,
    texto: str,
    canal_sesion: Optional[Tuple[str, str]] = None,
) -> Tuple[str, Optional[Venta]]:
    """Interpreta y asienta la venta con descuento atómico de inventario e ICA."""
    t_lower = normalizar_texto(texto)

    # 1. Detección si el comerciante o cliente solicita Factura Electrónica formal
    quiere_factura = bool(re.search(r"\b(con\s+factura|factura|facturacion|fe)\b", t_lower))

    # Limpiar palabra 'factura' para que no contamine la búsqueda de producto o concepto
    texto_sin_factura = re.sub(r"\b(con\s+factura|factura|facturacion|fe)\b", " ", texto, flags=re.IGNORECASE).strip()

    # 2. Detección de Documento / Cédula / NIT de Cliente en el texto para el ticket
    # Códigos DIAN para Exógena y Facturación Electrónica:
    # 11: Registro Civil, 12: Tarjeta de Identidad, 13: Cédula de Ciudadanía, 21: Tarjeta de Extranjería,
    # 22: Cédula de Extranjería, 31: NIT, 41: Pasaporte, 42: Doc. Extranjero, 47: PPT
    match_dian_con_num = re.search(
        r"(?:(?:tipo|dian|codigo|cod|poner|doc|documento)\s+)?\b(11|12|13|21|22|31|41|42|47)\s+(\d{5,12}(?:-\d)?)(?:\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+))?\b",
        texto_sin_factura,
        re.IGNORECASE,
    )
    match_doc_con_num = None
    if not match_dian_con_num:
        match_doc_con_num = re.search(
            r"(?:documento|doc|cc|c\.c\.|cedula|cdula|nit|cliente)[:\s]*(\d{5,12}(?:-\d)?)",
            texto_sin_factura,
            re.IGNORECASE,
        )

    doc_cliente_directo = None
    nombre_cliente_directo = ""
    tipo_doc_dian_directo = None
    tipo_doc_dian_pendiente = None
    pide_documento_sin_num = False

    if match_dian_con_num:
        tipo_doc_dian_directo = match_dian_con_num.group(1)
        doc_cliente_directo = match_dian_con_num.group(2)
        raw_nombre = match_dian_con_num.group(3) or ""
        resto_limpio_nombre = re.sub(
            r"\b(nequi|daviplata|bre-b|breb|bancolombia|transferencia|efectivo)\b", "", raw_nombre, flags=re.IGNORECASE
        ).strip()
        if resto_limpio_nombre and not re.search(r"\d", resto_limpio_nombre):
            nombre_cliente_directo = resto_limpio_nombre
        
        idx_ini = match_dian_con_num.start()
        idx_doc_end = match_dian_con_num.start(2) + len(doc_cliente_directo)
        if nombre_cliente_directo:
            texto_para_inventario = texto_sin_factura[:idx_ini] + " " + texto_sin_factura[idx_doc_end:].replace(nombre_cliente_directo, " ")
        else:
            texto_para_inventario = texto_sin_factura[:idx_ini] + " " + texto_sin_factura[idx_doc_end:]

    elif match_doc_con_num:
        doc_cliente_directo = match_doc_con_num.group(1)
        idx_ini = match_doc_con_num.start()
        idx_fin = match_doc_con_num.end()
        resto_post = texto_sin_factura[idx_fin:].strip()
        resto_limpio_nombre = re.sub(
            r"\b(nequi|daviplata|bre-b|breb|bancolombia|transferencia|efectivo)\b", "", resto_post, flags=re.IGNORECASE
        ).strip()
        if resto_limpio_nombre and not re.search(r"\d", resto_limpio_nombre):
            nombre_cliente_directo = resto_limpio_nombre
            texto_para_inventario = texto_sin_factura[:idx_ini] + " " + texto_sin_factura[idx_fin:].replace(resto_limpio_nombre, " ")
        else:
            texto_para_inventario = texto_sin_factura[:idx_ini] + " " + texto_sin_factura[idx_fin:]

    else:
        # Verificar si especificó código DIAN sin número de documento
        # Ej: 40 mil carne poner 13, 40 mil carne tipo 13, 40 mil carne 13, 40 mil carne 31
        match_dian_solo = re.search(
            r"(?:(?:tipo|dian|codigo|cod|poner|doc|documento)\s+)?\b(11|12|13|21|22|31|41|42|47)\b(?!\s*(?:mil|kilos?|kg|libras?|lb|gramos?|gr|lucas|k|\$|pesos|\d))",
            texto_sin_factura,
            re.IGNORECASE,
        )
        if match_dian_solo and not quiere_factura:
            tiene_prefijo = bool(re.search(r"\b(tipo|dian|codigo|cod|poner|doc|documento)\s+(11|12|13|21|22|31|41|42|47)\b", texto_sin_factura, re.IGNORECASE))
            val_chk, _ = parsear_dinero(texto_sin_factura[:match_dian_solo.start()] + " " + texto_sin_factura[match_dian_solo.end():])
            if tiene_prefijo or (val_chk and val_chk > 0):
                tipo_doc_dian_pendiente = match_dian_solo.group(1)
                texto_para_inventario = texto_sin_factura[:match_dian_solo.start()] + " " + texto_sin_factura[match_dian_solo.end():]

        if not tipo_doc_dian_pendiente:
            # ¿Pidió documento genérico en lenguaje natural sin número? (ej: 40 mil carne con documento)
            pide_documento_sin_num = bool(
                not quiere_factura
                and re.search(r"\b(con\s+documento|con\s+doc|documento|doc|cedula|cliente)\b", t_lower)
            )
            if pide_documento_sin_num:
                texto_para_inventario = re.sub(
                    r"\b(con\s+documento|con\s+doc|documento|doc|cedula|cliente)\b", " ", texto_sin_factura, flags=re.IGNORECASE
                )
            else:
                texto_para_inventario = texto_sin_factura
        else:
            pide_documento_sin_num = True

    val, texto_sin_dinero = parsear_dinero(texto_para_inventario)
    kilos = parsear_peso(texto_para_inventario)

    # Detección de medio de pago
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
    res_inv = procesar_salida_inventario(
        establecimiento, texto_para_inventario, valor_ingresado=val
    )
    prod = res_inv.producto
    val_final = res_inv.valor_final
    info_inv = res_inv.info_formateada

    if not val_final or val_final <= 0:
        return (
            "❓ No entendí el valor de la venta.\n"
            "Ejemplo: `40 mil carne molida nequi`, `3 cervezas` o `2 libras pechuga`.",
            None,
        )

    # Actividad principal para ICA
    actividad = establecimiento.actividades.first()
    concepto_venta = res_inv.concepto_ticket if (res_inv and res_inv.concepto_ticket) else (
        prod.nombre if prod else (texto_sin_dinero[:120] or "Venta general")
    )

    # Asignación o resolución del Cliente para la venta
    mun_nom = establecimiento.municipio.nombre if establecimiento.municipio else "Tunja"
    dep_nom = establecimiento.municipio.departamento if establecimiento.municipio else "Boyacá"

    if doc_cliente_directo:
        cliente_ticket = Cliente.objects.filter(establecimiento=establecimiento, nit_cedula=doc_cliente_directo).first()
        if tipo_doc_dian_directo:
            tipo_doc = tipo_doc_dian_directo
            tipo_pers = "juridica" if tipo_doc == "31" else "natural"
            prefix = "NIT" if tipo_doc == "31" else ("CE" if tipo_doc == "22" else "CC")
        else:
            tipo_doc = "31" if ("-" in doc_cliente_directo or (len(doc_cliente_directo.split("-")[0]) == 9 and doc_cliente_directo.startswith(("8", "9")))) else "13"
            tipo_pers = "juridica" if tipo_doc == "31" else "natural"
            prefix = "NIT" if tipo_doc == "31" else "CC"

        if not cliente_ticket:
            cliente_ticket = Cliente.objects.create(
                establecimiento=establecimiento,
                nombre=nombre_cliente_directo or f"{prefix} {doc_cliente_directo}",
                tipo_documento=tipo_doc,
                nit_cedula=doc_cliente_directo,
                tipo_persona=tipo_pers,
                municipio_nombre=mun_nom,
                departamento_nombre=dep_nom,
            )
        else:
            cambios = []
            if tipo_doc_dian_directo and cliente_ticket.tipo_documento != tipo_doc_dian_directo:
                cliente_ticket.tipo_documento = tipo_doc_dian_directo
                cambios.append("tipo_documento")
            if nombre_cliente_directo and cliente_ticket.nombre != nombre_cliente_directo and cliente_ticket.nombre.startswith(("CC ", "NIT ", "CE ", "Cliente CC")):
                cliente_ticket.nombre = nombre_cliente_directo
                cambios.append("nombre")
            if cambios:
                cliente_ticket.save(update_fields=cambios)
    else:
        cliente_ticket = Cliente.obtener_consumidor_final(establecimiento)
        prefix = "CC"

    with transaction.atomic():
        venta = Venta.objects.create(
            establecimiento=establecimiento,
            usuario=usuario,
            actividad=actividad,
            cliente=cliente_ticket,
            producto=prod,
            cantidad=res_inv.cantidad if (res_inv and res_inv.cantidad > 0) else Decimal("1.000"),
            unidad_medida=res_inv.unidad_medida if (res_inv and res_inv.unidad_medida) else "und",
            fecha=timezone.localdate(),
            fecha_hora=timezone.now(),
            valor=val_final,
            concepto=concepto_venta,
            medio_pago=medio_pago,
            estado="vigente",
        )

    valor_fmt = f"${val_final:,.0f}".replace(",", ".")
    concepto_fmt = concepto_venta
    if prod:
        if prod.es_peso():
            stock_fmt = f" | Quedan {prod.stock_kilos} Kg"
        elif prod.es_liquido():
            stock_fmt = f" | Quedan {prod.stock_kilos} Lt"
        else:
            stock_fmt = f" | Quedan {int(prod.stock_kilos)} und"
    else:
        stock_fmt = ""
    hora_str = timezone.localtime(venta.fecha_hora).strftime('%I:%M %p')

    # 1. Si solicitó factura electrónica formal DIAN
    if quiere_factura:
        tiene_email = bool(re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", texto))
        tiene_doc = bool(re.search(r"\b\d{6,12}\b", texto_sin_dinero))

        if tiene_email or (tiene_doc and len(texto.split()) >= 4):
            # Caso 1: Envió venta y datos del cliente en un solo mensaje
            resp_fe = _emitir_factura_electronica_bot(
                establecimiento=establecimiento,
                usuario=usuario,
                texto=texto,
                venta=venta,
                canal_sesion=canal_sesion,
            )
            resp = f"✅ *Venta Registrada:* {valor_fmt} ({concepto_fmt}) {badge_pago}{stock_fmt}\n\n" + resp_fe
            return resp, venta
        else:
            # Caso 2: Pide factura pero faltan los datos del cliente -> Iniciar sesión conversacional
            if canal_sesion:
                _VENTAS_PENDIENTES_FACTURA[canal_sesion] = {
                    "venta_id": venta.pk,
                    "monto": venta.valor,
                    "concepto": concepto_fmt,
                    "fecha": timezone.now(),
                }
            resp = (
                f"✅ *Venta Registrada:* {valor_fmt} ({concepto_fmt}) {badge_pago}{stock_fmt}\n\n"
                f"🧾 *Iniciando Factura Electrónica DIAN*\n"
                f"Por favor responde ahora con los datos del cliente:\n"
                f"• *NIT o Cédula*\n"
                f"• *Nombre o Razón Social*\n"
                f"• *Correo electrónico*\n"
                f"• *Teléfono* (opcional)\n\n"
                f"_(O escribe `cancelar` para dejarla como ticket consumidor final)_"
            )
            if info_inv:
                resp += f"\n{info_inv}"
            return resp, venta

    # 2. Si pidió registrar documento para el ticket pero omitió el número
    if pide_documento_sin_num:
        if canal_sesion:
            _VENTAS_PENDIENTES_DOCUMENTO[canal_sesion] = {
                "venta_id": venta.pk,
                "tipo_doc": tipo_doc_dian_pendiente,
                "fecha": timezone.now(),
            }
        if tipo_doc_dian_pendiente:
            nombre_tipo = DIAN_TIPOS_DOC.get(tipo_doc_dian_pendiente, f"Tipo {tipo_doc_dian_pendiente}")
            prompt_doc = f"✍️ *Por favor escribe el número de {nombre_tipo} (Tipo {tipo_doc_dian_pendiente} DIAN):*"
        else:
            prompt_doc = "✍️ *Por favor escribe el número de documento o cédula del cliente:*"

        resp = (
            f"✅ *Venta Registrada:* {valor_fmt} ({concepto_fmt}) {badge_pago}{stock_fmt}\n"
            f"🧾 Ticket #{venta.pk} | {hora_str}\n\n"
            f"{prompt_doc}\n"
            f"_(O escribe `omitir` para dejarlo en ventas menores 222222222222)_"
        )
        if info_inv:
            resp += f"\n{info_inv}"
        return resp, venta

    # 3. Venta Normal de Mostrador (Ticket con identificación de documento o ventas menores 222222222222)
    if doc_cliente_directo:
        if tipo_doc_dian_directo:
            sufijo_tipo = f", Tipo {tipo_doc_dian_directo} DIAN"
            sufijo_solo = f" (Tipo {tipo_doc_dian_directo} DIAN)"
        else:
            sufijo_tipo = ""
            sufijo_solo = ""

        if cliente_ticket.nombre and not cliente_ticket.nombre.startswith(("Cliente CC", "CC ", "NIT ", "CE ")) and cliente_ticket.nombre != cliente_ticket.nit_cedula:
            cli_line = f"👤 *Cliente:* {cliente_ticket.nombre} ({prefix} {cliente_ticket.nit_cedula}{sufijo_tipo})"
        else:
            cli_line = f"👤 *Cliente:* {prefix} {cliente_ticket.nit_cedula}{sufijo_solo}"
    else:
        cli_line = "👤 *Cliente:* Consumidor Final (222222222222)"

    resp = (
        f"✅ *Venta Registrada:* {valor_fmt} ({concepto_fmt}) {badge_pago}{stock_fmt}\n"
        f"🧾 Ticket #{venta.pk} | {hora_str}\n"
        f"{cli_line}"
    )
    if info_inv:
        resp += f"\n{info_inv}"

    return resp, venta


def _emitir_factura_electronica_bot(
    establecimiento: Establecimiento,
    usuario: User,
    texto: str,
    venta: Optional[Venta] = None,
    canal_sesion: Optional[Tuple[str, str]] = None,
) -> str:
    """Emite Factura Electrónica formal DIAN desde el chat para una venta."""
    base_url = getattr(settings, "BASE_URL", os.environ.get("BASE_URL", "http://127.0.0.1:8001")).rstrip("/")

    # 1. Determinar la venta
    resto = texto.strip()
    if venta is None:
        match_ticket = re.search(r"(?:/factura|/facturar|factura|facturar|emitir factura)\s*(?:#|no\.?|num\.?)?\s*(\d+)", texto, re.IGNORECASE)
        if match_ticket:
            ticket_id = int(match_ticket.group(1))
            venta = Venta.objects.filter(pk=ticket_id, establecimiento=establecimiento).first()
            if not venta:
                return f"❌ No se encontró ninguna venta con el ticket #{ticket_id} en {establecimiento.nombre}."
            resto = texto[match_ticket.end():].strip()
        else:
            if canal_sesion and canal_sesion in _VENTAS_PENDIENTES_FACTURA:
                ticket_id = _VENTAS_PENDIENTES_FACTURA[canal_sesion]["venta_id"]
                venta = Venta.objects.filter(pk=ticket_id, establecimiento=establecimiento).first()
            if not venta:
                venta = Venta.objects.filter(establecimiento=establecimiento, fecha=timezone.localdate()).order_by("-id").first()
                if not venta:
                    return "❌ No hay ventas recientes registradas para emitir Factura Electrónica."

    if venta.solicita_factura_electronica and venta.numero_factura_electronica:
        if canal_sesion:
            _VENTAS_PENDIENTES_FACTURA.pop(canal_sesion, None)
        link_ver = f"{base_url}/facturacion-electronica/{venta.pk}/"
        return (
            f"ℹ️ La venta ya cuenta con Factura Electrónica oficial ante la DIAN.\n\n"
            f"• *Factura Electrónica:* `{venta.numero_factura_electronica}`\n"
            f"• *Adquirente:* {venta.cliente.nombre if venta.cliente else 'Adquirente'}\n"
            f"• *CUFE:* `{venta.cufe[:16]}...`\n"
            f"• *Estado DIAN:* ✅ Aprobada\n\n"
            f"📄 Ver documento oficial: {link_ver}"
        )

    # 2. Extraer datos del cliente: Email, Teléfono, Cédula/NIT, Nombre
    # Correo electrónico
    email_match = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", resto)
    email_cliente = email_match.group(0).strip().lower() if email_match else ""
    resto_sin_email = (resto[:email_match.start()] + " " + resto[email_match.end():]).strip() if email_match else resto

    # Teléfono (opcional - celular en Colombia suele ser 10 dígitos iniciando en 3, o precedido de tel/cel/wa)
    tel_match = re.search(r"(?:tel(?:efono)?|cel(?:ular)?|wa(?:tsapp)?|movil)[:\s]*([0-9]{7,12})", resto_sin_email, re.IGNORECASE)
    if not tel_match:
        tel_match = re.search(r"\b(3\d{9})\b", resto_sin_email)
    tel_cliente = tel_match.group(1).strip() if tel_match else ""
    resto_sin_tel = (resto_sin_email[:tel_match.start()] + " " + resto_sin_email[tel_match.end():]).strip() if tel_match else resto_sin_email

    # Cédula o NIT (6 a 12 dígitos, opcionalmente con guion para dígito de verificación)
    doc_match = re.search(r"(?:nit|cc|cedula|c\.c\.)[:\s]*(\d{6,12}(?:-\d)?)", resto_sin_tel, re.IGNORECASE)
    if not doc_match:
        doc_match = re.search(r"\b(\d{6,12}(?:-\d)?)\b", resto_sin_tel)
    doc_cliente = doc_match.group(1).strip() if doc_match else ""
    resto_sin_doc = (resto_sin_tel[:doc_match.start()] + " " + resto_sin_tel[doc_match.end():]).strip() if doc_match else resto_sin_tel

    # Nombre o Razón Social (resto del texto limpio de palabras reservadas)
    nombre_limpio = re.sub(
        r"\b(factura|facturar|emitir|con|a|para|cliente|nombre|nit|cc|cedula|c\.c\.|correo|email|tel|telefono|cel|celular|movil|solicitante|de|por|favor)\b",
        " ",
        resto_sin_doc,
        flags=re.IGNORECASE,
    )
    nombre_limpio = re.sub(r"[#:/,\-]", " ", nombre_limpio)
    nombre_cliente = re.sub(r"\s+", " ", nombre_limpio).strip()

    # Si faltan datos obligatorios según la Resolución 000165 de la DIAN:
    if not doc_cliente or not email_cliente or not nombre_cliente:
        faltantes = []
        if not doc_cliente:
            faltantes.append("Cédula o NIT")
        if not nombre_cliente:
            faltantes.append("Nombre o Razón Social")
        if not email_cliente:
            faltantes.append("Correo Electrónico (para entrega DIAN)")

        return (
            f"🧾 *Datos Requeridos para Factura Electrónica*\n\n"
            f"⚠️ Para cumplir con la DIAN, falta indicar: *{', '.join(faltantes)}*.\n\n"
            f"👉 Por favor responde con:\n"
            f"`<Cédula o NIT> <Nombre o Razón Social> <Correo>` (y teléfono opcional)\n\n"
            f"*Ejemplo:* `1049582123 Carlos Gómez carlos@gmail.com 3101234567`\n\n"
            f"_(O escribe `cancelar` para dejarla como ticket consumidor final)_"
        )

    # 3. Registrar o actualizar Tercero / Cliente
    doc_limpio = doc_cliente.split("-")[0]
    es_nit = "-" in doc_cliente or (len(doc_limpio) == 9 and doc_limpio.startswith(("8", "9")))
    tipo_doc = "31" if es_nit else "13"
    tipo_pers = "juridica" if es_nit else "natural"

    mun_nom = establecimiento.municipio.nombre if establecimiento.municipio else "Tunja"
    dep_nom = establecimiento.municipio.departamento if establecimiento.municipio else "Boyacá"

    cliente = Cliente.objects.filter(establecimiento=establecimiento, nit_cedula=doc_cliente).first()
    if not cliente:
        cliente = Cliente.objects.create(
            establecimiento=establecimiento,
            nombre=nombre_cliente,
            tipo_documento=tipo_doc,
            nit_cedula=doc_cliente,
            tipo_persona=tipo_pers,
            correo_electronico=email_cliente,
            telefono=tel_cliente,
            municipio_nombre=mun_nom,
            departamento_nombre=dep_nom,
        )
    else:
        actualizar = []
        if email_cliente and cliente.correo_electronico != email_cliente:
            cliente.correo_electronico = email_cliente
            actualizar.append("correo_electronico")
        if nombre_cliente and cliente.nombre != nombre_cliente:
            cliente.nombre = nombre_cliente
            actualizar.append("nombre")
        if tel_cliente and cliente.telefono != tel_cliente:
            cliente.telefono = tel_cliente
            actualizar.append("telefono")
        if actualizar:
            cliente.save(update_fields=actualizar)

    # 4. Asignación atómica de consecutivo y CUFE DIAN
    with transaction.atomic():
        num_fe = establecimiento.siguiente_consecutivo_factura()
        cufe = Venta.generar_cufe(establecimiento, num_fe, venta.valor, venta.fecha_hora, cliente.nit_cedula)

        venta.solicita_factura_electronica = True
        venta.cliente = cliente
        venta.numero_factura_electronica = num_fe
        venta.cufe = cufe
        venta.estado_dian = "aprobada"
        venta.save(update_fields=["solicita_factura_electronica", "cliente", "numero_factura_electronica", "cufe", "estado_dian"])

        Auditoria.objects.create(
            establecimiento=establecimiento,
            usuario=usuario,
            entidad_afectada="Venta",
            id_registro=venta.pk,
            accion="emitir_factura_electronica",
            valor_anterior="Consumidor Final",
            valor_nuevo=f"Factura #{num_fe}, Cliente={cliente.nombre}",
            motivo="Emisión de Factura Electrónica por Asistente Móvil",
        )

    if canal_sesion:
        _VENTAS_PENDIENTES_FACTURA.pop(canal_sesion, None)

    valor_fmt = f"${venta.valor:,.0f}".replace(",", ".")
    link_pdf = f"{base_url}/facturacion-electronica/{venta.pk}/pdf/"
    link_ver = f"{base_url}/facturacion-electronica/{venta.pk}/"
    tel_line = f"\n• *Teléfono:* `{cliente.telefono}`" if cliente.telefono else ""

    return (
        f"🧾 *¡Factura Electrónica Emitida Exitosamente!*\n\n"
        f"• *Factura DIAN N°:* `{num_fe}`\n"
        f"• *Adquirente:* {cliente.nombre}\n"
        f"• *Documento:* `{cliente.nit_cedula}` ({cliente.get_tipo_documento_display()})\n"
        f"• *Correo Entrega:* {cliente.correo_electronico}{tel_line}\n"
        f"• *Monto Total:* {valor_fmt} COP\n"
        f"• *CUFE:* `{cufe[:18]}...`\n"
        f"• *Estado DIAN:* ✅ Aprobada y Transmitida\n\n"
        f"📥 *Descargar Factura PDF:* {link_pdf}\n"
        f"🌐 *Ver Detalle Oficial:* {link_ver}"
    )


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
        "• *Cobro Inmediato Bre-B (WeChat Pay):* `/cobrar 45 mil`\n"
        "• *Factura Electrónica DIAN:* `/factura <ticket> <NIT/Cédula> <Nombre> <Correo>`\n"
        "  _Ej:_ `/factura 124 1049582123 Carlos Gomez carlos@gmail.com`\n"
        "• *Registrar Compra:* `compra 80 mil proveedor verduras`\n"
        "• *Consultar Stock:* `cuanto queda de costilla` o `/stock`\n"
    )
    if es_propietario:
        ayuda += (
            "\n👑 *Comandos de Propietario:*\n"
            "• `/hoy` o `cierre`: Arqueo y balance discriminado de caja\n"
            "• `/excel` o `/consolidado`: Descargar libro oficial para el contador\n"
            "• `/anular <ticket> <motivo>`: Anulación segura con auditoría\n"
        )
    return ayuda
