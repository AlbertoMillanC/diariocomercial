import csv
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO, StringIO

from django.http import HttpResponse
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from .models import Compra, Retencion, Venta


def pesos(value):
    if value is None:
        value = 0
    n = Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"$ {int(n):,}".replace(",", ".")


def _movimientos(establecimiento, desde, hasta):
    filas = []
    ventas = Venta.objects.filter(
        establecimiento=establecimiento, fecha__range=(desde, hasta)
    ).select_related("usuario", "actividad")
    for v in ventas:
        filas.append(
            {
                "fecha": v.fecha,
                "tipo": "Venta",
                "detalle": v.concepto or "",
                "valor": v.valor,
                "estado": v.estado,
                "usuario": v.usuario.get_full_name() or v.usuario.username,
            }
        )
    compras = Compra.objects.filter(
        establecimiento=establecimiento, fecha__range=(desde, hasta)
    ).select_related("usuario")
    for c in compras:
        filas.append(
            {
                "fecha": c.fecha,
                "tipo": "Compra",
                "detalle": c.proveedor,
                "valor": c.valor,
                "estado": c.estado,
                "usuario": c.usuario.get_full_name() or c.usuario.username,
            }
        )
    rets = Retencion.objects.filter(
        establecimiento=establecimiento, fecha__range=(desde, hasta)
    ).select_related("usuario")
    for r in rets:
        filas.append(
            {
                "fecha": r.fecha,
                "tipo": "Retención",
                "detalle": r.tercero or r.get_tipo_display(),
                "valor": r.valor,
                "estado": r.estado,
                "usuario": r.usuario.get_full_name() or r.usuario.username,
            }
        )
    filas.sort(key=lambda x: str(x["fecha"]), reverse=True)
    return filas


def texto_consolidado(est, desde, hasta, ingresos, egresos, retenciones, ica, neto, por_ciiu):
    lineas = [
        f"Consolidado {est.nombre}",
        f"NIT: {est.nit or '—'}",
        f"Periodo: {desde.strftime('%d/%m/%Y')} a {hasta.strftime('%d/%m/%Y')}",
        "",
        f"Ingresos brutos: {pesos(ingresos)}",
        f"ICA estimado: {pesos(ica)}",
        f"Compras y gastos: {pesos(egresos)}",
        f"Retenciones (ventas a empresa): {pesos(retenciones)}",
        f"Base neta estimada: {pesos(neto)}",
        "",
        "Por actividad CIIU:",
    ]
    if por_ciiu:
        for f in por_ciiu:
            act = f["actividad"]
            lineas.append(
                f"- {act.codigo} {act.descripcion} ({act.tarifa_x_mil} x mil) | "
                f"ventas {pesos(f['bruto'])} | ICA {pesos(f['ica'])}"
            )
    else:
        lineas.append("- Sin actividades registradas")
    lineas.append("")
    lineas.append("Detalle de movimientos (incluye anulados, marcados):")
    for m in _movimientos(est, desde, hasta):
        marca = "" if m["estado"] == "vigente" else f" [{m['estado']}]"
        lineas.append(
            f"- {m['fecha'].strftime('%d/%m/%Y')} {m['tipo']} {m['detalle']} "
            f"{pesos(m['valor'])}{marca} ({m['usuario']})"
        )
    return "\n".join(lineas)


def respuesta_csv(est, desde, hasta, ingresos, egresos, retenciones, ica, neto, por_ciiu):
    buf = StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Establecimiento", est.nombre])
    writer.writerow(["Periodo", f"{desde} a {hasta}"])
    writer.writerow([])
    writer.writerow(["Concepto", "Valor"])
    writer.writerow(["Ingresos brutos", ingresos])
    writer.writerow(["ICA estimado", ica])
    writer.writerow(["Compras y gastos", egresos])
    writer.writerow(["Retenciones", retenciones])
    writer.writerow(["Base neta estimada", neto])
    writer.writerow([])
    writer.writerow(["CIIU", "Descripcion", "Tarifa x mil", "Ventas", "ICA"])
    for f in por_ciiu:
        act = f["actividad"]
        writer.writerow([act.codigo, act.descripcion, act.tarifa_x_mil, f["bruto"], f["ica"]])
    writer.writerow([])
    writer.writerow(["Fecha", "Tipo", "Detalle", "Valor", "Estado", "Usuario"])
    for m in _movimientos(est, desde, hasta):
        writer.writerow([m["fecha"], m["tipo"], m["detalle"], m["valor"], m["estado"], m["usuario"]])
    writer.writerow([])
    writer.writerow(["CARLOS ALBERTO MILLAN CASTAÑO / DESARROLLO WEB"])
    resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="consolidado_{desde}_{hasta}.csv"'
    return resp


def respuesta_pdf(est, desde, hasta, ingresos, egresos, retenciones, ica, neto, por_ciiu):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    ancho, alto = letter
    y = alto - 50

    def linea(texto, size=11, salto=16):
        nonlocal y
        if y < 65:
            # Pie de pagina en pagina previa
            pdf.setFont("Helvetica-Oblique", 8)
            pdf.drawString(40, 25, "CARLOS ALBERTO MILLAN CASTAÑO / DESARROLLO WEB — DiarioComercial")
            pdf.showPage()
            y = alto - 50
        pdf.setFont("Helvetica", size)
        pdf.drawString(40, y, texto[:110])
        y -= salto

    pdf.setTitle(f"Consolidado {est.nombre}")
    linea(f"DiarioComercial — {est.nombre}", 14, 20)
    linea(f"Periodo {desde.strftime('%d/%m/%Y')} a {hasta.strftime('%d/%m/%Y')}", 11, 22)
    linea(f"Ingresos brutos: {pesos(ingresos)}")
    linea(f"ICA estimado: {pesos(ica)}")
    linea(f"Compras y gastos: {pesos(egresos)}")
    linea(f"Retenciones: {pesos(retenciones)}")
    linea(f"Base neta estimada: {pesos(neto)}", 11, 22)
    linea("Por actividad CIIU:", 12, 18)
    for f in por_ciiu:
        act = f["actividad"]
        linea(
            f"{act.codigo} {act.descripcion} ({act.tarifa_x_mil} x mil) "
            f"ventas {pesos(f['bruto'])} ICA {pesos(f['ica'])}",
            10,
            14,
        )
    linea("", 10, 10)
    linea("Movimientos", 12, 18)
    for m in _movimientos(est, desde, hasta):
        marca = "" if m["estado"] == "vigente" else f" [{m['estado']}]"
        linea(
            f"{m['fecha'].strftime('%d/%m/%Y')} {m['tipo']} {m['detalle']} "
            f"{pesos(m['valor'])}{marca}",
            9,
            13,
        )
    # Pie de pagina final
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(40, 25, "CARLOS ALBERTO MILLAN CASTAÑO / DESARROLLO WEB — DiarioComercial")
    pdf.save()
    buffer.seek(0)
    resp = HttpResponse(buffer.read(), content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="consolidado_{desde}_{hasta}.pdf"'
    return resp
