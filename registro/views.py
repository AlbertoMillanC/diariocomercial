from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from .forms import CompraForm, RetencionForm, VentaForm
from .models import Auditoria, Compra, EnvioReporte, Establecimiento, Retencion, Venta


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
    egresos = sum((c.valor for c in compras), Decimal("0"))
    retenciones = sum((r.valor for r in rets), Decimal("0"))
    return ingresos, egresos, retenciones, ingresos - egresos


@login_required
def inicio(request):
    perfil = _perfil(request.user)
    desde, hasta = _rango_mes()
    ingresos = egresos = retenciones = neto = Decimal("0")
    movimientos = []
    if perfil:
        est = perfil.establecimiento
        ingresos, egresos, retenciones, neto = _totales(est, desde, hasta)
        for v in Venta.objects.filter(establecimiento=est).order_by("-fecha", "-id")[:8]:
            movimientos.append(
                {"fecha": v.fecha, "tipo": "Venta", "clase": "", "detalle": v.concepto, "valor": v.valor}
            )
        for c in Compra.objects.filter(establecimiento=est).order_by("-fecha", "-id")[:4]:
            movimientos.append(
                {"fecha": c.fecha, "tipo": "Compra", "clase": "compra", "detalle": c.proveedor, "valor": c.valor}
            )
        movimientos.sort(key=lambda x: x["fecha"], reverse=True)
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
            "neto": neto,
            "movimientos": movimientos,
        },
    )


@login_required
def venta_nueva(request):
    perfil = _perfil(request.user)
    if not perfil:
        messages.error(request, "El usuario no tiene establecimiento asignado.")
        return redirect("inicio")
    form = VentaForm(request.POST or None, initial={"fecha": date.today()})
    if request.method == "POST" and form.is_valid():
        venta = form.save(commit=False)
        venta.establecimiento = perfil.establecimiento
        venta.usuario = request.user
        venta.estado = "vigente"
        if venta.valor <= 0:
            messages.error(request, "El valor tiene que ser mayor a cero.")
        else:
            venta.save()
            messages.success(request, "Venta guardada.")
            return redirect("inicio")
    return render(request, "venta_form.html", {"form": form})


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
    return _guardar_movimiento(request, RetencionForm, "retencion_form.html", "Retención guardada.")


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
        for v in Venta.objects.filter(establecimiento=est, fecha__range=(desde, hasta)).order_by("-fecha"):
            filas.append({"obj": v, "tipo": "Venta", "clase": "", "detalle": v.concepto, "key": "venta"})
    if tipo in ("todos", "compra"):
        for c in Compra.objects.filter(establecimiento=est, fecha__range=(desde, hasta)).order_by("-fecha"):
            filas.append({"obj": c, "tipo": "Compra", "clase": "compra", "detalle": c.proveedor, "key": "compra"})
    if tipo in ("todos", "retencion"):
        for r in Retencion.objects.filter(establecimiento=est, fecha__range=(desde, hasta)).order_by("-fecha"):
            filas.append({"obj": r, "tipo": "Retención", "clase": "ret", "detalle": r.tercero or r.get_tipo_display(), "key": "retencion"})
    filas.sort(key=lambda x: x["obj"].fecha, reverse=True)
    ingresos, egresos, retenciones, neto = _totales(est, desde, hasta)
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
    antes = f"{obj.fecha}|{obj.valor}"
    form = Formulario(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        guardado = form.save()
        _audit(request.user, tipo, guardado, "editar", antes, f"{guardado.fecha}|{guardado.valor}")
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
        messages.error(request, "Solo el propietario configura el correo.")
        return redirect("inicio")
    est = perfil.establecimiento
    if request.method == "POST":
        correo = request.POST.get("correo_reportes", "").strip()
        est.correo_reportes = correo
        est.save()
        messages.success(request, "Correo del contador actualizado.")
        return redirect("configuracion")
    return render(request, "configuracion.html", {"establecimiento": est})


@login_required
def enviar_reporte(request):
    perfil = _perfil(request.user)
    if not perfil or not perfil.es_propietario():
        messages.error(request, "Solo el propietario envía el consolidado.")
        return redirect("inicio")
    est = perfil.establecimiento
    desde, hasta = _rango_mes()
    ingresos, egresos, retenciones, neto = _totales(est, desde, hasta)
    if request.method == "POST":
        destino = est.correo_reportes
        if not destino:
            messages.error(request, "Primero configure el correo en Configuración.")
            return redirect("configuracion")
        cuerpo = (
            f"Consolidado {est.nombre}\n"
            f"Periodo: {desde} a {hasta}\n"
            f"Ingresos: {ingresos}\n"
            f"Compras: {egresos}\n"
            f"Retenciones: {retenciones}\n"
            f"Base neta estimada: {neto}\n"
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
            "neto": neto,
        },
    )
