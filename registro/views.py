from datetime import date
from decimal import Decimal
import urllib.parse

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.core.mail import send_mail
from django.db.models import Q, Count, Sum
from django.http import Http404, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from .forms import (
    ActividadCIIUForm,
    CompraForm,
    ItemPedidoForm,
    LoginForm,
    MotivoVentaForm,
    ProductoForm,
    RetencionForm,
    UsuarioNegocioForm,
    VentaForm,
)
from .models import (
    ActividadCIIU,
    Auditoria,
    Compra,
    EnvioReporte,
    ItemPedido,
    MotivoVenta,
    Perfil,
    Producto,
    Retencion,
    Venta,
    Establecimiento,
    Municipio,
    VinculoCanal,
    TokenVinculacion,
    TransaccionBreB,
)
from .bot_service import generar_token_vinculacion
from .bre_b_service import generar_qr_dinamico_bre_b, procesar_confirmacion_bre_b
from .print_service import generar_bytes_escpos_recibo
from .tax_engine import liquidar_declaracion_sugerida_ica, generar_resumen_exogena_anual
from .reportes import respuesta_csv, respuesta_pdf, texto_consolidado
from .inventario_service import (
    procesar_salida_inventario,
    revertir_salida_inventario,
    sincronizar_productos_agotados,
)


class LoginDiario(LoginView):
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_invalid(self, form):
        username = (self.request.POST.get("username") or "").strip()
        user = User.objects.filter(username=username).first()
        Auditoria.objects.create(
            usuario=user,
            entidad_afectada="sesion",
            id_registro=user.pk if user else 0,
            accion="login_fallido",
            valor_nuevo=username,
            motivo="Credenciales inválidas o usuario inactivo",
        )
        return super().form_invalid(form)


def _perfil(user):
    return getattr(user, "perfil", None)


def _rango_mes():
    hoy = date.today()
    return hoy.replace(day=1), hoy


def _parse_rango(data):
    hoy = date.today()
    periodo = (data.get("periodo") or "").strip()
    if periodo == "hoy":
        return hoy, hoy, False
    elif periodo == "este_mes":
        return hoy.replace(day=1), hoy, False
    elif periodo == "mes_anterior":
        from datetime import timedelta
        primer_dia_este_mes = hoy.replace(day=1)
        ultimo_dia_mes_ant = primer_dia_este_mes - timedelta(days=1)
        primer_dia_mes_ant = ultimo_dia_mes_ant.replace(day=1)
        return primer_dia_mes_ant, ultimo_dia_mes_ant, False
    elif periodo == "ano":
        return hoy.replace(month=1, day=1), hoy, False

    mes_desde, hoy = _rango_mes()
    desde = parse_date(data.get("desde") or "") or mes_desde
    hasta = parse_date(data.get("hasta") or "") or hoy
    return desde, hasta, hasta < desde


def _totales(establecimiento, desde, hasta, usuario_id=None):
    ventas = Venta.objects.filter(
        establecimiento=establecimiento, estado="vigente", fecha__range=(desde, hasta)
    )
    compras = Compra.objects.filter(
        establecimiento=establecimiento, estado="vigente", fecha__range=(desde, hasta)
    )
    rets = Retencion.objects.filter(
        establecimiento=establecimiento, estado="vigente", fecha__range=(desde, hasta)
    )
    if usuario_id:
        ventas = ventas.filter(usuario_id=usuario_id)
        compras = compras.filter(usuario_id=usuario_id)
        rets = rets.filter(usuario_id=usuario_id)
    ingresos = sum((v.valor for v in ventas), Decimal("0"))
    ica = sum((v.ica_estimado for v in ventas), Decimal("0"))
    egresos = sum((c.valor for c in compras), Decimal("0"))
    retenciones = sum((r.valor for r in rets), Decimal("0"))
    return ingresos, egresos, retenciones, ica, ingresos - egresos


def _ica_por_ciiu(establecimiento, desde, hasta):
    filas = []
    for act in ActividadCIIU.objects.filter(establecimiento=establecimiento):
        ventas = Venta.objects.filter(
            establecimiento=establecimiento,
            actividad=act,
            estado="vigente",
            fecha__range=(desde, hasta),
        )
        bruto = sum((v.valor for v in ventas), Decimal("0"))
        ica = sum((v.ica_estimado for v in ventas), Decimal("0"))
        filas.append({"actividad": act, "bruto": bruto, "ica": ica})
    return filas


def _snapshot(obj):
    partes = [
        f"fecha={getattr(obj, 'fecha', '')}",
        f"valor={obj.valor}",
        f"estado={obj.estado}",
    ]
    if hasattr(obj, "concepto"):
        partes.append(f"concepto={obj.concepto}")
    if hasattr(obj, "tipo_cliente"):
        partes.append(f"cliente={obj.tipo_cliente}")
    if hasattr(obj, "proveedor"):
        partes.append(f"proveedor={obj.proveedor}")
    if hasattr(obj, "tercero"):
        partes.append(f"tercero={obj.tercero}")
    return "|".join(partes)


def _audit(user, entidad, obj, accion, antes, despues, motivo=""):
    Auditoria.objects.create(
        usuario=user,
        entidad_afectada=entidad,
        id_registro=obj.pk if obj is not None else 0,
        accion=accion,
        valor_anterior=str(antes),
        valor_nuevo=str(despues),
        motivo=motivo,
    )


def inicio(request):
    if not request.user.is_authenticated:
        return render(request, "landing.html", {
            "version": "2.0",
        })
    perfil = _perfil(request.user)
    desde, hasta, rango_malo = _parse_rango(request.GET)
    ingresos = egresos = retenciones = ica = neto = Decimal("0")
    movimientos = []
    por_ciiu = []
    if rango_malo:
        messages.error(request, "La fecha hasta no puede ser menor que la fecha desde.")
    elif perfil:
        est = perfil.establecimiento
        ingresos, egresos, retenciones, ica, neto = _totales(est, desde, hasta)
        por_ciiu = _ica_por_ciiu(est, desde, hasta)
        for v in (
            Venta.objects.filter(establecimiento=est)
            .select_related("usuario", "motivo")
            .order_by("-fecha_hora", "-id")[:8]
        ):
            movimientos.append(
                {
                    "fecha": v.fecha_hora,
                    "tipo": "Venta",
                    "clase": "",
                    "detalle": v.concepto or (v.motivo.nombre if v.motivo else ""),
                    "valor": v.valor,
                    "usuario": v.usuario.get_full_name() or v.usuario.username,
                }
            )
        for c in (
            Compra.objects.filter(establecimiento=est)
            .select_related("usuario")
            .order_by("-fecha", "-id")[:4]
        ):
            movimientos.append(
                {
                    "fecha": c.fecha,
                    "tipo": "Compra",
                    "clase": "compra",
                    "detalle": c.proveedor,
                    "valor": c.valor,
                    "usuario": c.usuario.get_full_name() or c.usuario.username,
                }
            )
        movimientos.sort(key=lambda x: str(x["fecha"]), reverse=True)
        movimientos = movimientos[:8]
    return render(
        request,
        "inicio.html",
        {
            "desde": desde,
            "hasta": hasta,
            "ingresos": ingresos,
            "egresos": egresos,
            "retenciones": retenciones,
            "ica": ica,
            "neto": neto,
            "movimientos": movimientos,
            "por_ciiu": por_ciiu,
        },
    )


def _motivos_data(establecimiento):
    data = []
    for m in MotivoVenta.objects.filter(establecimiento=establecimiento):
        data.append(
            {
                "id": m.id,
                "nombre": m.nombre,
                "actividad": m.actividad_id,
                "predeterminado": m.es_predeterminado,
            }
        )
    return data


@login_required
def venta_nueva(request):
    perfil = _perfil(request.user)
    if not perfil:
        messages.error(request, "El usuario no tiene establecimiento asignado.")
        return redirect("inicio")
    est = perfil.establecimiento
    if not ActividadCIIU.objects.filter(establecimiento=est).exists():
        messages.error(request, "Primero registre las actividades CIIU en Configuración.")
        return redirect("configuracion")
    form = VentaForm(request.POST or None, establecimiento=est)
    if request.method == "POST" and form.is_valid():
        venta = form.save(commit=False)
        venta.establecimiento = est
        venta.usuario = request.user
        venta.estado = "vigente"
        nuevo = form.cleaned_data.get("nuevo_motivo", "").strip()
        if nuevo:
            motivo, _ = MotivoVenta.objects.get_or_create(
                establecimiento=est,
                actividad=venta.actividad,
                nombre=nuevo,
                defaults={"es_predeterminado": False},
            )
            venta.motivo = motivo
        if not venta.motivo and venta.actividad:
            pred = MotivoVenta.objects.filter(
                actividad=venta.actividad, es_predeterminado=True
            ).first()
            venta.motivo = pred
        if venta.valor <= 0:
            messages.error(request, "El valor tiene que ser mayor a cero.")
        else:
            venta.save()
            # Descontar inventario si el concepto o motivo coincide con un producto
            texto_concepto = f"{venta.concepto or ''} {venta.motivo.nombre if venta.motivo else ''}".strip()
            prod, kilos, libras, _, info_stock = procesar_salida_inventario(
                est, texto_concepto, venta.valor
            )
            if venta.tipo_cliente == "empresa":
                ret_val = form.cleaned_data.get("retencion_valor") or Decimal("0")
                if ret_val > 0:
                    Retencion.objects.create(
                        establecimiento=est,
                        usuario=request.user,
                        venta=venta,
                        fecha=venta.fecha,
                        tipo=form.cleaned_data.get("retencion_tipo") or "ica",
                        valor=ret_val,
                        tercero=form.cleaned_data.get("tercero") or "",
                        estado="vigente",
                    )
            msg_exito = "Venta guardada."
            if prod and kilos > 0:
                msg_exito += f" Existencias actualizadas: -{kilos} Kg (-{libras} lb) de '{prod.nombre}'. Stock: {prod.stock_kilos} Kg."
            messages.success(request, msg_exito)
            return redirect("inicio")
    return render(
        request,
        "venta_form.html",
        {"form": form, "motivos_data": _motivos_data(est)},
    )


def _guardar_movimiento(request, form_class, plantilla, mensaje):
    perfil = _perfil(request.user)
    if not perfil:
        messages.error(request, "El usuario no tiene establecimiento asignado.")
        return redirect("inicio")
    form = form_class(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.establecimiento = perfil.establecimiento
        obj.usuario = request.user
        obj.estado = "vigente"
        if obj.valor <= 0:
            messages.error(request, "El valor tiene que ser mayor a cero.")
        else:
            obj.save()
            messages.success(request, mensaje)
            return redirect("inicio")
    return render(request, plantilla, {"form": form})


@login_required
def compra_nueva(request):
    return _guardar_movimiento(request, CompraForm, "compra_form.html", "Compra guardada.")


@login_required
def retencion_nueva(request):
    messages.info(
        request,
        "Si la retención nació de una venta a empresa, regístrela ahí. "
        "Si le retuvieron por fuera, puede cargarla aquí.",
    )
    return _guardar_movimiento(
        request, RetencionForm, "retencion_form.html", "Retención guardada."
    )


def _modelo(tipo):
    if tipo == "venta":
        return Venta, VentaForm
    if tipo == "compra":
        return Compra, CompraForm
    if tipo == "retencion":
        return Retencion, RetencionForm
    raise Http404()


@login_required
def historial(request):
    perfil = _perfil(request.user)
    if not perfil:
        return redirect("inicio")
    est = perfil.establecimiento
    desde, hasta, rango_malo = _parse_rango(request.GET)
    tipo = request.GET.get("tipo") or "todos"
    usuario_id = request.GET.get("usuario") or ""
    usuarios_negocio = User.objects.filter(perfil__establecimiento=est).order_by("first_name", "username")
    filas = []
    if rango_malo:
        messages.error(request, "La fecha hasta no puede ser menor que la fecha desde.")
        ingresos = egresos = retenciones = ica = neto = Decimal("0")
    else:
        if tipo in ("todos", "venta"):
            qs_ventas = (
                Venta.objects.filter(establecimiento=est, fecha__range=(desde, hasta))
                .select_related("usuario", "actividad")
                .order_by("-fecha_hora")
            )
            if usuario_id:
                qs_ventas = qs_ventas.filter(usuario_id=usuario_id)
            for v in qs_ventas:
                ciiu = v.actividad.codigo if v.actividad else ""
                filas.append(
                    {
                        "obj": v,
                        "tipo": "Venta",
                        "clase": "",
                        "detalle": f"{v.concepto} {ciiu} {v.get_tipo_cliente_display()}".strip(),
                        "key": "venta",
                        "cuando": v.fecha_hora,
                        "usuario": v.usuario.get_full_name() or v.usuario.username,
                    }
                )
        if tipo in ("todos", "compra"):
            qs_compras = (
                Compra.objects.filter(establecimiento=est, fecha__range=(desde, hasta))
                .select_related("usuario")
                .order_by("-fecha")
            )
            if usuario_id:
                qs_compras = qs_compras.filter(usuario_id=usuario_id)
            for c in qs_compras:
                filas.append(
                    {
                        "obj": c,
                        "tipo": "Compra",
                        "clase": "compra",
                        "detalle": c.proveedor,
                        "key": "compra",
                        "cuando": c.fecha,
                        "usuario": c.usuario.get_full_name() or c.usuario.username,
                    }
                )
        if tipo in ("todos", "retencion"):
            qs_rets = (
                Retencion.objects.filter(establecimiento=est, fecha__range=(desde, hasta))
                .select_related("usuario")
                .order_by("-fecha")
            )
            if usuario_id:
                qs_rets = qs_rets.filter(usuario_id=usuario_id)
            for r in qs_rets:
                filas.append(
                    {
                        "obj": r,
                        "tipo": "Retención (empresa)",
                        "clase": "ret",
                        "detalle": r.tercero or r.get_tipo_display(),
                        "key": "retencion",
                        "cuando": r.fecha,
                        "usuario": r.usuario.get_full_name() or r.usuario.username,
                    }
                )
        filas.sort(key=lambda x: str(x["cuando"]), reverse=True)
        ingresos, egresos, retenciones, ica, neto = _totales(est, desde, hasta, usuario_id=usuario_id)
    return render(
        request,
        "historial.html",
        {
            "filas": filas,
            "desde": desde,
            "hasta": hasta,
            "tipo": tipo,
            "usuarios_negocio": usuarios_negocio,
            "usuario_filtro": usuario_id,
            "ingresos": ingresos,
            "egresos": egresos,
            "retenciones": retenciones,
            "ica": ica,
            "neto": neto,
            "puede_editar": perfil.es_propietario(),
        },
    )


@login_required
def editar_movimiento(request, tipo, pk):
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        messages.error(request, "Solo el propietario puede editar.")
        return redirect("historial")
    Modelo, Formulario = _modelo(tipo)
    obj = get_object_or_404(Modelo, pk=pk, establecimiento=perfil.establecimiento)
    antes = _snapshot(obj)
    kwargs = {"instance": obj}
    if tipo == "venta":
        form = Formulario(request.POST or None, establecimiento=perfil.establecimiento, **kwargs)
    else:
        form = Formulario(request.POST or None, **kwargs)
    if request.method == "POST" and form.is_valid():
        motivo = (request.POST.get("motivo_cambio") or "").strip()
        if not motivo:
            messages.error(request, "Indique el motivo del cambio para dejarlo en auditoría.")
        elif form.instance.valor is not None and form.instance.valor <= 0:
            messages.error(request, "El valor tiene que ser mayor a cero.")
        else:
            guardado = form.save()
            if tipo == "venta" and guardado.tipo_cliente != "empresa":
                Retencion.objects.filter(venta=guardado, estado="vigente").update(estado="anulado")
            _audit(
                request.user,
                tipo,
                guardado,
                "editar",
                antes,
                _snapshot(guardado),
                motivo,
            )
            messages.success(
                request,
                f"{tipo.capitalize()} #{guardado.pk} actualizada con éxito (motivo: \"{motivo}\"). Cambio registrado en auditoría.",
            )
            return redirect("historial")
    return render(request, "editar_form.html", {"form": form, "tipo": tipo})


@login_required
def anular_movimiento(request, tipo, pk):
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        messages.error(request, "Solo el propietario puede anular.")
        return redirect("historial")
    Modelo, _form = _modelo(tipo)
    obj = get_object_or_404(Modelo, pk=pk, establecimiento=perfil.establecimiento)
    if request.method == "POST":
        motivo = (request.POST.get("motivo") or "").strip()
        if not motivo:
            messages.error(request, "Debe indicar el motivo de la anulación.")
        else:
            antes = _snapshot(obj)
            obj.estado = "anulado"
            obj.save()
            info_inv = ""
            if tipo == "venta":
                for ret in Retencion.objects.filter(venta=obj, estado="vigente"):
                    ret_antes = _snapshot(ret)
                    ret.estado = "anulado"
                    ret.save()
                    _audit(
                        request.user,
                        "retencion",
                        ret,
                        "anular",
                        ret_antes,
                        _snapshot(ret),
                        "Anulada junto con la venta",
                    )
                info_inv = revertir_salida_inventario(perfil.establecimiento, obj)
            _audit(request.user, tipo, obj, "anular", antes, _snapshot(obj), motivo)
            msg_anulacion = f"{tipo.capitalize()} #{obj.pk} anulada exitosamente (motivo: \"{motivo}\")."
            if info_inv:
                msg_anulacion += f" {info_inv.replace('*', '')}"
            messages.success(request, msg_anulacion)
            return redirect("historial")
    return render(request, "anular_confirm.html", {"obj": obj, "tipo": tipo})


@login_required
def configuracion(request):
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        messages.error(request, "Solo el propietario configura el negocio.")
        return redirect("inicio")
    est = perfil.establecimiento
    form_ciiu = ActividadCIIUForm(prefix="ciiu")
    form_motivo = MotivoVentaForm(prefix="motivo")
    form_motivo.fields["actividad"].queryset = ActividadCIIU.objects.filter(establecimiento=est)
    form_usuario = UsuarioNegocioForm()

    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "correo":
            est.correo_reportes = request.POST.get("correo_reportes", "").strip()
            est.save()
            messages.success(request, "Correo del contador actualizado.")
            return redirect("configuracion")
        elif accion == "ciiu":
            form_ciiu = ActividadCIIUForm(request.POST, prefix="ciiu")
            if form_ciiu.is_valid():
                act = form_ciiu.save(commit=False)
                act.establecimiento = est
                act.save()
                messages.success(request, f"Actividad CIIU {act.codigo} registrada.")
                return redirect("configuracion")
        elif accion == "motivo":
            form_motivo = MotivoVentaForm(request.POST, prefix="motivo")
            form_motivo.fields["actividad"].queryset = ActividadCIIU.objects.filter(
                establecimiento=est
            )
            if form_motivo.is_valid():
                mot = form_motivo.save(commit=False)
                mot.establecimiento = est
                mot.save()
                if mot.es_predeterminado:
                    MotivoVenta.objects.filter(actividad=mot.actividad).exclude(pk=mot.pk).update(
                        es_predeterminado=False
                    )
                messages.success(request, "Motivo de venta guardado.")
                return redirect("configuracion")
        elif accion == "usuario":
            form_usuario = UsuarioNegocioForm(request.POST)
            if form_usuario.is_valid():
                data = form_usuario.cleaned_data
                nuevo = User.objects.create_user(
                    username=data["username"],
                    password=data["password"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                )
                Perfil.objects.create(
                    user=nuevo, establecimiento=est, rol=data["rol"]
                )
                messages.success(request, f"Usuario {nuevo.username} creado.")
                return redirect("configuracion")
        elif accion in ("activar", "desactivar"):
            uid = request.POST.get("user_id")
            objetivo = get_object_or_404(Perfil, pk=uid, establecimiento=est)
            if objetivo.user_id == request.user.id:
                messages.error(request, "No puede desactivarse usted mismo.")
            elif accion == "desactivar" and objetivo.es_propietario():
                otros = Perfil.objects.filter(
                    establecimiento=est, rol="propietario", user__is_active=True
                ).exclude(pk=objetivo.pk)
                if not otros.exists():
                    messages.error(request, "Debe quedar al menos un propietario activo.")
                else:
                    objetivo.user.is_active = False
                    objetivo.user.save()
                    messages.success(request, f"{objetivo.user.username} quedó inactivo.")
            elif accion == "desactivar":
                objetivo.user.is_active = False
                objetivo.user.save()
                messages.success(request, f"{objetivo.user.username} quedó inactivo.")
            else:
                objetivo.user.is_active = True
                objetivo.user.save()
                messages.success(request, f"{objetivo.user.username} quedó activo.")
            return redirect("configuracion")
        elif accion == "generar_token_movil":
            uid = request.POST.get("user_id") or request.user.id
            usuario_obj = get_object_or_404(User, pk=uid)
            generar_token_vinculacion(usuario_obj, est, duracion_minutos=120)
            messages.success(request, f"Nuevo código de vinculación generado para {usuario_obj.username}.")
            return redirect("configuracion")
        elif accion == "revocar_sesiones_moviles":
            total_revocados = VinculoCanal.objects.filter(establecimiento=est, activo=True).update(activo=False)
            TokenVinculacion.objects.filter(establecimiento=est, usado=False).update(usado=True)
            _audit(request.user, "canal_movil", None, "revocar_todo", total_revocados, 0, "Kill-switch de seguridad ejecutado")
            messages.warning(request, f"¡Seguridad activada! Se revocaron todas las sesiones móviles ({total_revocados} dispositivos desconectados).")
            return redirect("configuracion")
        elif accion == "desvincular_canal":
            vid = request.POST.get("vinculo_id")
            v = get_object_or_404(VinculoCanal, pk=vid, establecimiento=est)
            v.activo = False
            v.save()
            _audit(request.user, "canal_movil", v, "desvincular", True, False, f"Desvinculado {v.canal} {v.identificador_externo}")
            messages.success(request, f"Canal {v.canal} de {v.usuario.username} desvinculado.")
            return redirect("configuracion")

    from django.utils import timezone
    usuarios = Perfil.objects.filter(establecimiento=est).select_related("user").order_by("rol", "user__username")
    vinculos_activos = VinculoCanal.objects.filter(establecimiento=est, activo=True).select_related("usuario").order_by("-fecha_creacion")
    token_activo = TokenVinculacion.objects.filter(establecimiento=est, usuario=request.user, usado=False, expira__gt=timezone.now()).order_by("-id").first()
    pin_6 = token_activo.token.replace("auth_", "") if token_activo else None
    deep_link = f"https://t.me/DiarioComercial_bot?start={token_activo.token}" if token_activo else None

    return render(
        request,
        "configuracion.html",
        {
            "establecimiento": est,
            "actividades": ActividadCIIU.objects.filter(establecimiento=est),
            "motivos": MotivoVenta.objects.filter(establecimiento=est),
            "form_ciiu": form_ciiu,
            "form_motivo": form_motivo,
            "form_usuario": form_usuario,
            "usuarios": usuarios,
            "vinculos_activos": vinculos_activos,
            "token_activo": token_activo,
            "pin_6": pin_6,
            "deep_link": deep_link,
        },
    )


@login_required
def auditoria(request):
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        messages.error(request, "Solo el propietario consulta la auditoría.")
        return redirect("inicio")
    filas = (
        Auditoria.objects.filter(
            Q(usuario__perfil__establecimiento=perfil.establecimiento)
            | Q(usuario__isnull=True, entidad_afectada="sesion")
        )
        .select_related("usuario")
        .order_by("-fecha_hora")[:200]
    )
    return render(request, "auditoria.html", {"filas": filas})


@login_required
def enviar_reporte(request):
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        messages.error(request, "Solo el propietario envía el consolidado.")
        return redirect("inicio")
    est = perfil.establecimiento
    data = request.POST if request.method == "POST" else request.GET
    desde, hasta, rango_malo = _parse_rango(data)
    if rango_malo:
        messages.error(request, "La fecha hasta no puede ser menor que la fecha desde.")
        ingresos = egresos = retenciones = ica = neto = Decimal("0")
        por_ciiu = []
    else:
        ingresos, egresos, retenciones, ica, neto = _totales(est, desde, hasta)
        por_ciiu = _ica_por_ciiu(est, desde, hasta)

    formato = request.GET.get("formato")
    if formato in ("pdf", "csv") and not rango_malo:
        args = (est, desde, hasta, ingresos, egresos, retenciones, ica, neto, por_ciiu)
        if formato == "pdf":
            return respuesta_pdf(*args)
        return respuesta_csv(*args)

    if request.method == "POST" and not rango_malo:
        destino = est.correo_reportes
        if not destino:
            messages.error(request, "Primero configure el correo en Configuración.")
            return redirect("configuracion")
        cuerpo = texto_consolidado(
            est, desde, hasta, ingresos, egresos, retenciones, ica, neto, por_ciiu
        )
        try:
            send_mail(
                f"Consolidado {est.nombre} {desde} - {hasta}",
                cuerpo,
                None,
                [destino],
            )
            estado = "enviado"
            messages.success(request, f"Reporte enviado a {destino}.")
        except Exception as e:
            estado = "fallido"
            messages.error(
                request,
                f"No se pudo enviar: {e}. Puede reintentar o descargar el PDF/CSV.",
            )
        EnvioReporte.objects.create(
            establecimiento=est,
            usuario=request.user,
            periodo_inicio=desde,
            periodo_fin=hasta,
            correo_destino=destino,
            estado_envio=estado,
            total_ingresos=ingresos,
            total_egresos=egresos,
            total_retenciones=retenciones,
            total_ica=ica,
        )
        return redirect("enviar_reporte")

    envios = EnvioReporte.objects.filter(establecimiento=est).order_by("-fecha_envio")[:12]
    return render(
        request,
        "enviar.html",
        {
            "establecimiento": est,
            "desde": desde,
            "hasta": hasta,
            "ingresos": ingresos,
            "egresos": egresos,
            "retenciones": retenciones,
            "ica": ica,
            "neto": neto,
            "por_ciiu": por_ciiu,
            "envios": envios,
        },
    )


@login_required
def inventario_lista(request):
    perfil = _perfil(request.user)
    if not perfil:
        return redirect("inicio")
    est = perfil.establecimiento

    categoria = request.GET.get("categoria") or "todas"
    qs = Producto.objects.filter(establecimiento=est)
    if categoria != "todas":
        qs = qs.filter(categoria=categoria)

    form = ProductoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        prod = form.save(commit=False)
        prod.establecimiento = est
        prod.save()
        messages.success(request, f"Producto '{prod.nombre}' agregado al inventario.")
        return redirect("inventario")

    total_kilos = sum((p.stock_kilos for p in qs), Decimal("0"))
    valor_inventario = sum((p.stock_kilos * p.precio_kilo for p in qs), Decimal("0"))

    return render(
        request,
        "inventario.html",
        {
            "productos": qs,
            "form": form,
            "categoria_actual": categoria,
            "total_kilos": total_kilos,
            "valor_inventario": valor_inventario,
            "puede_editar": perfil.es_propietario(),
        },
    )


@login_required
def inventario_ajustar(request, pk):
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        messages.error(request, "Solo el propietario puede modificar inventario.")
        return redirect("inventario")
    prod = get_object_or_404(Producto, pk=pk, establecimiento=perfil.establecimiento)
    if request.method == "POST":
        nuevo_stock = request.POST.get("stock_kilos")
        nuevo_precio = request.POST.get("precio_kilo")
        try:
            if nuevo_stock:
                prod.stock_kilos = Decimal(nuevo_stock)
            if nuevo_precio:
                prod.precio_kilo = Decimal(nuevo_precio)
            prod.save()
            messages.success(request, f"Existencias y precio de '{prod.nombre}' actualizados.")
        except Exception as e:
            messages.error(request, f"Error al actualizar: {e}")
    return redirect("inventario")


@login_required
def pedidos_lista(request):
    perfil = _perfil(request.user)
    if not perfil:
        return redirect("inicio")
    est = perfil.establecimiento

    # Auto sincronizar productos en cero a pedidos
    sincronizar_productos_agotados(est)

    estado_filtro = request.GET.get("estado") or "pendiente"
    origen_filtro = request.GET.get("origen") or "todos"

    qs = ItemPedido.objects.filter(establecimiento=est)
    if estado_filtro != "todos":
        qs = qs.filter(estado=estado_filtro)
    if origen_filtro != "todos":
        qs = qs.filter(origen=origen_filtro)

    form = ItemPedidoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.establecimiento = est
        item.origen = "manual"
        item.save()
        messages.success(request, f"Producto '{item.nombre_producto}' agregado al pedido de compras.")
        return redirect("pedidos")

    total_pendientes = ItemPedido.objects.filter(establecimiento=est, estado="pendiente").count()
    total_agotados = ItemPedido.objects.filter(establecimiento=est, estado="pendiente", origen="agotado").count()
    total_solicitados = ItemPedido.objects.filter(establecimiento=est, estado="pendiente", origen="solicitado").count()

    # Construir enlace de WhatsApp para enviar pedido formal al proveedor
    items_pendientes = ItemPedido.objects.filter(establecimiento=est, estado="pendiente").order_by("categoria", "nombre_producto")
    if items_pendientes.exists():
        lineas = [f"📋 *PEDIDO DE COMPRA - {est.nombre.upper()}*"]
        lineas.append("Hola, solicito despacho de los siguientes productos:\n")
        for item in items_pendientes:
            cant = f" - {item.cantidad_sugerida:g} {item.unidad}" if item.cantidad_sugerida > 0 else ""
            obs = f" ({item.observacion})" if item.observacion else ""
            lineas.append(f"▫️ *{item.nombre_producto}*{cant}{obs}")
        lineas.append("\n_Generado automáticamente desde Diario Comercial._")
        msg_wa = "\n".join(lineas)
        whatsapp_pedido_url = f"https://wa.me/?text={urllib.parse.quote(msg_wa)}"
    else:
        whatsapp_pedido_url = ""

    return render(
        request,
        "pedidos.html",
        {
            "items": qs,
            "form": form,
            "estado_filtro": estado_filtro,
            "origen_filtro": origen_filtro,
            "total_pendientes": total_pendientes,
            "total_agotados": total_agotados,
            "total_solicitados": total_solicitados,
            "whatsapp_pedido_url": whatsapp_pedido_url,
            "puede_editar": perfil.es_propietario(),
        },
    )


@login_required
def pedido_cambiar_estado(request, pk, accion):
    perfil = _perfil(request.user)
    if not perfil:
        return redirect("inicio")
    item = get_object_or_404(ItemPedido, pk=pk, establecimiento=perfil.establecimiento)

    if accion == "comprar":
        item.estado = "comprado"
        item.save()
        messages.success(request, f"'{item.nombre_producto}' marcado como COMPRADO.")
    elif accion == "descartar":
        item.estado = "descartado"
        item.save()
        messages.info(request, f"'{item.nombre_producto}' descartado del pedido.")
    elif accion == "pendiente":
        item.estado = "pendiente"
        item.save()
        messages.info(request, f"'{item.nombre_producto}' reactivado como PENDIENTE.")

    return redirect("pedidos")


@login_required
def instrucciones(request):
    return render(request, "instrucciones.html")


# ============================================================================
# FASE 3: COBRO RÁPIDO BRE-B & IMPRESIÓN ESC/POS
# ============================================================================

@login_required
def api_generar_cobro_bre_b(request):
    """Genera transacción EMVCo y payload QR para el modal de cobro en mostrador."""
    perfil = _perfil(request.user)
    if not perfil:
        return JsonResponse({"error": "Usuario sin perfil comercial"}, status=403)

    try:
        monto_raw = request.POST.get("monto") or request.GET.get("monto") or "0"
        monto = Decimal(str(monto_raw).replace(",", ".").strip())
    except Exception:
        return JsonResponse({"error": "Formato de monto inválido"}, status=400)

    if monto <= Decimal("0"):
        return JsonResponse({"error": "El monto a cobrar debe ser mayor a $0"}, status=400)

    est = perfil.establecimiento
    ciudad = est.municipio.nombre if est.municipio else "TUNJA"

    tx, payload_emvco = generar_qr_dinamico_bre_b(
        establecimiento=est,
        monto=monto,
        ciudad=ciudad,
    )

    return JsonResponse({
        "referencia": tx.referencia_unica,
        "token_visual": tx.token_visual_corto,
        "monto": float(tx.monto),
        "payload_emvco": payload_emvco,
        "llave": tx.llave_utilizada,
        "expira_en_segundos": 120,
    })


@login_required
def api_status_bre_b(request, referencia):
    """Consulta de estado polling con TTL para el modal de caja."""
    tx = get_object_or_404(TransaccionBreB, referencia_unica=referencia)
    return JsonResponse({
        "referencia": tx.referencia_unica,
        "token_visual": tx.token_visual_corto,
        "estado": tx.estado,
        "aprobada": tx.estado == "aprobada",
        "monto": float(tx.monto),
        "venta_id": tx.venta_id if tx.venta else None,
    })


@login_required
def api_mock_webhook_bre_b(request, referencia):
    """Simulador de confirmación BanRep para demostraciones y pruebas de usabilidad."""
    import secrets
    tx = get_object_or_404(TransaccionBreB, referencia_unica=referencia)
    banrep_id = f"BANREP-SIM-{secrets.token_hex(4).upper()}"
    exito, msg, venta, _ = procesar_confirmacion_bre_b(
        referencia_unica=referencia,
        monto_acreditado=tx.monto,
        banrep_transaction_id=banrep_id,
        banco_origen="Bancolombia / Nequi",
        nombre_pagador="Cliente Mostrador",
    )
    return JsonResponse({
        "exito": exito,
        "mensaje": msg,
        "venta_id": venta.pk if venta else None,
        "banrep_id": banrep_id,
    })


@login_required
def imprimir_ticket_escpos(request, pk, ancho=58):
    """Retorna el binario puro ESC/POS para impresión térmica en papel de 58mm u 80mm."""
    perfil = _perfil(request.user)
    if not perfil:
        return HttpResponse("No autorizado", status=403)
    venta = get_object_or_404(Venta, pk=pk, establecimiento=perfil.establecimiento)
    bytes_raw = generar_bytes_escpos_recibo(venta, ancho_papel_mm=int(ancho))
    resp = HttpResponse(bytes_raw, content_type="application/octet-stream")
    resp["Content-Disposition"] = f'inline; filename="ticket_{venta.pk}_{ancho}mm.bin"'
    return resp


# ============================================================================
# FASE 4: DECLARACIÓN SUGERIDA ICA TUNJA (ACUERDO 0032 DE 2020)
# ============================================================================

@login_required
def declaracion_sugerida_ica_view(request):
    """Borrador de precálculo sugerido del Impuesto de Industria y Comercio de Tunja."""
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        messages.error(request, "El módulo de declaración tributaria está reservado para el propietario.")
        return redirect("inicio")

    desde, hasta, _ = _parse_rango(request.GET)
    est = perfil.establecimiento
    liq = liquidar_declaracion_sugerida_ica(est, desde, hasta)

    return render(
        request,
        "tributario/declaracion_ica.html",
        {
            "establecimiento": est,
            "liq": liq,
            "desde": desde,
            "hasta": hasta,
            "hoy": timezone.localdate(),
        },
    )


@login_required
def exportar_declaracion_ica(request):
    """Descarga anexo oficial para el contador en formato texto tabulado."""
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        return HttpResponse("No autorizado", status=403)

    desde, hasta, _ = _parse_rango(request.GET)
    est = perfil.establecimiento
    liq = liquidar_declaracion_sugerida_ica(est, desde, hasta)
    reng = liq.get("renglones", {})

    lineas = [
        f"ANEXO TRIBUTARIO ICA - {est.nombre.upper()}",
        f"MUNICIPIO: {liq.get('municipio', 'Tunja')}",
        f"NORMATIVA: {liq.get('normativa', 'Acuerdo 0032/2020')}",
        f"PERIODO: {desde} AL {hasta}",
        "--------------------------------------------------",
        f"1. Ingresos Brutos Totales: ${reng.get('1_ingresos_brutos', Decimal('0')):,.2f}",
        f"2. Menos Ingresos Fuera del Municipio: ${reng.get('2_ingresos_fuera_municipio', Decimal('0')):,.2f}",
        f"3. Menos Devoluciones y Descuentos: ${reng.get('3_devoluciones_descuentos', Decimal('0')):,.2f}",
        f"4. Base Gravable Neta: ${reng.get('4_base_gravable_neta', Decimal('0')):,.2f}",
        f"5. Impuesto Neto de Industria y Comercio: ${reng.get('5_impuesto_neto_ica', Decimal('0')):,.2f}",
        f"6. Avisos y Tableros (15%): ${reng.get('6_impuesto_avisos_tableros_15pct', Decimal('0')):,.2f}",
        f"7. Sobretasa Bomberil (5%): ${reng.get('7_sobretasa_bomberil', Decimal('0')):,.2f}",
        f"8. Total Impuesto a Cargo: ${reng.get('8_total_impuesto_a_cargo', Decimal('0')):,.2f}",
        f"9. Menos Retenciones ICA que le practicaron: ${reng.get('9_menos_retenciones_ica_a_favor', Decimal('0')):,.2f}",
        f"10. SALDO SUGERIDO A PAGAR: ${reng.get('10_total_saldo_a_pagar', Decimal('0')):,.2f}",
        "--------------------------------------------------",
        "DISCLAIMER LEGAL: Borrador de precálculo sugerido para apoyo contable.",
        "Requiere revision y firma obligatoria de Contador Publico titulado.",
    ]
    contenido = "\r\n".join(lineas)
    resp = HttpResponse(contenido, content_type="text/plain; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="ica_tunja_{desde}_{hasta}.txt"'
    return resp


# ============================================================================
# FASE 5: DASHBOARD DE SUPER-ADMINISTRADOR SAAS
# ============================================================================

@login_required
def superadmin_dashboard(request):
    """Panel de monitoreo SaaS, control multi-tenant y cobranza."""
    if not request.user.is_superuser:
        messages.error(request, "Acceso restringido: El panel Super-Administrador es exclusivo para el operador central de la plataforma SaaS.")
        return redirect("inicio")

    hoy = timezone.localdate()

    total_comercios = Establecimiento.objects.count()
    comercios_activos = Establecimiento.objects.filter(estado="activo").count()
    comercios_plan_cero = Establecimiento.objects.filter(plan_suscripcion="lanzamiento_cero").count()
    comercios_mora = Establecimiento.objects.filter(plan_suscripcion="mora").count()

    volumen_hoy = Venta.objects.filter(fecha=hoy, estado="vigente").aggregate(Sum("valor"))["valor__sum"] or Decimal("0")
    total_ica_estimado = Venta.objects.filter(estado="vigente").aggregate(Sum("ica_estimado"))["ica_estimado__sum"] or Decimal("0")

    comercios_qs = Establecimiento.objects.select_related("municipio").all().order_by("-id")
    lista_comercios = []
    for c in comercios_qs:
        dias_restantes = (c.fecha_fin_prueba - hoy).days if c.fecha_fin_prueba else 30
        lista_comercios.append({
            "obj": c,
            "dias_restantes": max(0, dias_restantes),
            "en_riesgo": dias_restantes <= 5,
        })

    contadores = (
        Establecimiento.objects.exclude(correo_reportes="")
        .values("correo_reportes")
        .annotate(
            total_clientes=Count("id", distinct=True),
            total_descargas=Count("envioreporte__id", distinct=True)
        )
        .order_by("-total_clientes")
    )

    return render(
        request,
        "superadmin/dashboard.html",
        {
            "total_comercios": total_comercios,
            "comercios_activos": comercios_activos,
            "comercios_plan_cero": comercios_plan_cero,
            "comercios_mora": comercios_mora,
            "volumen_hoy": volumen_hoy,
            "total_ica_estimado": total_ica_estimado,
            "comercios": lista_comercios,
            "contadores": contadores,
            "hoy": hoy,
        },
    )


@login_required
def superadmin_toggle_estado(request, pk):
    """Activa o suspende el servicio de un comercio al instante."""
    if not request.user.is_superuser:
        messages.error(request, "Acceso restringido: Se requieren permisos de Super-Administrador.")
        return redirect("inicio")

    est = get_object_or_404(Establecimiento, pk=pk)
    est.estado = "suspendido" if est.estado == "activo" else "activo"
    est.save(update_fields=["estado"])
    messages.info(request, f"Establecimiento '{est.nombre}' marcado como {est.estado.upper()}.")
    return redirect("superadmin_dashboard")

