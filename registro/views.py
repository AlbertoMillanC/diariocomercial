from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from .forms import (
    ActividadCIIUForm,
    CompraForm,
    MotivoVentaForm,
    RetencionForm,
    VentaForm,
)
from .models import (
    ActividadCIIU,
    Auditoria,
    Compra,
    EnvioReporte,
    MotivoVenta,
    Retencion,
    Venta,
)


def _perfil(user):
    return getattr(user, "perfil", None)


def _rango_mes():
    hoy = date.today()
    return hoy.replace(day=1), hoy


def _totales(establecimiento, desde, hasta):
    ventas = Venta.objects.filter(
        establecimiento=establecimiento, estado="vigente", fecha__range=(desde, hasta)
    )
    compras = Compra.objects.filter(
        establecimiento=establecimiento, estado="vigente", fecha__range=(desde, hasta)
    )
    rets = Retencion.objects.filter(
        establecimiento=establecimiento, estado="vigente", fecha__range=(desde, hasta)
    )
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


@login_required
def inicio(request):
    perfil = _perfil(request.user)
    desde, hasta = _rango_mes()
    ingresos = egresos = retenciones = ica = neto = Decimal("0")
    movimientos = []
    por_ciiu = []
    if perfil:
        est = perfil.establecimiento
        ingresos, egresos, retenciones, ica, neto = _totales(est, desde, hasta)
        por_ciiu = _ica_por_ciiu(est, desde, hasta)
        for v in Venta.objects.filter(establecimiento=est).order_by("-fecha_hora", "-id")[:8]:
            movimientos.append(
                {
                    "fecha": v.fecha_hora,
                    "tipo": "Venta",
                    "clase": "",
                    "detalle": v.concepto or (v.motivo.nombre if v.motivo else ""),
                    "valor": v.valor,
                }
            )
        for c in Compra.objects.filter(establecimiento=est).order_by("-fecha", "-id")[:4]:
            movimientos.append(
                {"fecha": c.fecha, "tipo": "Compra", "clase": "compra", "detalle": c.proveedor, "valor": c.valor}
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
            messages.success(request, "Venta guardada.")
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
        "Las retenciones solo aplican cuando la venta es a una empresa. Regístrelas en la venta, eligiendo «Empresa».",
    )
    return redirect("venta_nueva")


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
    desde = parse_date(request.GET.get("desde") or "") or _rango_mes()[0]
    hasta = parse_date(request.GET.get("hasta") or "") or date.today()
    tipo = request.GET.get("tipo") or "todos"
    filas = []
    if tipo in ("todos", "venta"):
        for v in Venta.objects.filter(establecimiento=est, fecha__range=(desde, hasta)).order_by("-fecha_hora"):
            ciiu = v.actividad.codigo if v.actividad else ""
            filas.append(
                {
                    "obj": v,
                    "tipo": "Venta",
                    "clase": "",
                    "detalle": f"{v.concepto} {ciiu} {v.get_tipo_cliente_display()}".strip(),
                    "key": "venta",
                    "cuando": v.fecha_hora,
                }
            )
    if tipo in ("todos", "compra"):
        for c in Compra.objects.filter(establecimiento=est, fecha__range=(desde, hasta)).order_by("-fecha"):
            filas.append(
                {"obj": c, "tipo": "Compra", "clase": "compra", "detalle": c.proveedor, "key": "compra", "cuando": c.fecha}
            )
    if tipo in ("todos", "retencion"):
        for r in Retencion.objects.filter(establecimiento=est, fecha__range=(desde, hasta)).order_by("-fecha"):
            filas.append(
                {
                    "obj": r,
                    "tipo": "Retención (empresa)",
                    "clase": "ret",
                    "detalle": r.tercero or r.get_tipo_display(),
                    "key": "retencion",
                    "cuando": r.fecha,
                }
            )
    filas.sort(key=lambda x: str(x["cuando"]), reverse=True)
    ingresos, egresos, retenciones, ica, neto = _totales(est, desde, hasta)
    return render(
        request,
        "historial.html",
        {
            "filas": filas,
            "desde": desde,
            "hasta": hasta,
            "tipo": tipo,
            "ingresos": ingresos,
            "egresos": egresos,
            "retenciones": retenciones,
            "ica": ica,
            "puede_editar": perfil.es_propietario(),
        },
    )


def _audit(user, entidad, obj, accion, antes, despues, motivo=""):
    Auditoria.objects.create(
        usuario=user,
        entidad_afectada=entidad,
        id_registro=obj.pk,
        accion=accion,
        valor_anterior=str(antes),
        valor_nuevo=str(despues),
        motivo=motivo,
    )


@login_required
def editar_movimiento(request, tipo, pk):
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        messages.error(request, "Solo el propietario puede editar.")
        return redirect("historial")
    Modelo, Formulario = _modelo(tipo)
    obj = get_object_or_404(Modelo, pk=pk, establecimiento=perfil.establecimiento)
    antes = f"{getattr(obj, 'fecha', '')}|{obj.valor}"
    kwargs = {"instance": obj}
    if tipo == "venta":
        form = Formulario(request.POST or None, establecimiento=perfil.establecimiento, **kwargs)
    else:
        form = Formulario(request.POST or None, **kwargs)
    if request.method == "POST" and form.is_valid():
        guardado = form.save()
        _audit(request.user, tipo, guardado, "editar", antes, f"{getattr(guardado, 'fecha', '')}|{guardado.valor}")
        messages.success(request, "Registro actualizado.")
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
        antes = obj.estado
        obj.estado = "anulado"
        obj.save()
        _audit(request.user, tipo, obj, "anular", antes, "anulado")
        messages.success(request, "Registro anulado.")
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

    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "correo":
            est.correo_reportes = request.POST.get("correo_reportes", "").strip()
            est.save()
            messages.success(request, "Correo del contador actualizado.")
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
            form_motivo.fields["actividad"].queryset = ActividadCIIU.objects.filter(establecimiento=est)
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
        return redirect("configuracion")

    return render(
        request,
        "configuracion.html",
        {
            "establecimiento": est,
            "actividades": ActividadCIIU.objects.filter(establecimiento=est),
            "motivos": MotivoVenta.objects.filter(establecimiento=est),
            "form_ciiu": form_ciiu,
            "form_motivo": form_motivo,
        },
    )


@login_required
def enviar_reporte(request):
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        messages.error(request, "Solo el propietario envía el consolidado.")
        return redirect("inicio")
    est = perfil.establecimiento
    desde, hasta = _rango_mes()
    ingresos, egresos, retenciones, ica, neto = _totales(est, desde, hasta)
    por_ciiu = _ica_por_ciiu(est, desde, hasta)
    if request.method == "POST":
        destino = est.correo_reportes
        if not destino:
            messages.error(request, "Primero configure el correo en Configuración.")
            return redirect("configuracion")
        lineas = [
            f"Consolidado {est.nombre}",
            f"Periodo: {desde} a {hasta}",
            f"Ingresos: {ingresos}",
            f"ICA estimado: {ica}",
            f"Compras: {egresos}",
            f"Retenciones (solo ventas a empresa): {retenciones}",
            "",
            "Por actividad CIIU:",
        ]
        for f in por_ciiu:
            lineas.append(
                f"- {f['actividad'].codigo} {f['actividad'].tarifa_x_mil} x mil | ventas {f['bruto']} | ICA {f['ica']}"
            )
        try:
            send_mail(
                f"Consolidado {est.nombre} {desde} - {hasta}",
                "\n".join(lineas),
                None,
                [destino],
            )
            estado = "enviado"
            messages.success(request, f"Reporte enviado a {destino}.")
        except Exception as e:
            estado = "fallido"
            messages.error(request, f"No se pudo enviar: {e}")
        EnvioReporte.objects.create(
            establecimiento=est,
            usuario=request.user,
            periodo_inicio=desde,
            periodo_fin=hasta,
            correo_destino=destino,
            estado_envio=estado,
            total_ingresos=ingresos,
            total_egresos=egresos,
        )
        return redirect("inicio")
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
        },
    )
