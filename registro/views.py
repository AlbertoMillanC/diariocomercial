from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import Compra, Retencion, Venta


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
