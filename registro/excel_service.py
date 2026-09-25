"""
registro/excel_service.py
Módulo de procesamiento e importación/exportación de Pedidos e Inventarios en Excel (.xlsx)
y Conciliación de Pagos Electrónicos para DiarioComercial.
Compatible con formatos estándar de software de punto de venta (POS) y ERPs de retail.
"""
import io
from decimal import Decimal
from typing import Dict, Any, List, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from django.utils import timezone
from django.db import transaction

from .models import Producto, ItemPedido, Compra, Auditoria, Establecimiento, Venta, TransaccionBreB
from .inventario_service import normalizar_texto


def generar_plantilla_pedido_excel() -> bytes:
    """
    Genera una plantilla Excel (.xlsx) estándar de la industria para pedidos e inventario.
    Incluye formato visual profesional, columnas estándar POS y 5 filas de ejemplo.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pedido e Inventario"

    # Paleta de colores institucional
    verde_header = "065F46"   # Verde esmeralda oscuro
    verde_claro = "ECFDF5"
    gris_borde = "D1D5DB"
    azul_info = "1E3A8A"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(start_color=verde_header, end_color=verde_header, fill_type="solid")
    border_thin = Border(
        left=Side(style="thin", color=gris_borde),
        right=Side(style="thin", color=gris_borde),
        top=Side(style="thin", color=gris_borde),
        bottom=Side(style="thin", color=gris_borde),
    )
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    # Banner superior explicativo
    ws.merge_cells("A1:H1")
    title_cell = ws["A1"]
    title_cell.value = "PLANTILLA ESTÁNDAR DE PEDIDO E INGRESO DE INVENTARIO - DIARIOCOMERCIAL"
    title_cell.font = Font(name="Calibri", size=13, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill(start_color=azul_info, end_color=azul_info, fill_type="solid")
    title_cell.alignment = align_center
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:H2")
    sub_cell = ws["A2"]
    sub_cell.value = "Instrucciones: Ingrese los productos recibidos. Si el producto ya existe (por Código o Nombre), el sistema SUMARÁ la cantidad a sus existencias actuales. Si es nuevo, lo creará automáticamente."
    sub_cell.font = Font(name="Calibri", size=9, italic=True, color="374151")
    sub_cell.alignment = align_center
    ws.row_dimensions[2].height = 20

    # Encabezados de columnas (Fila 4)
    headers = [
        ("CODIGO_BARRAS", "Código de barras EAN-13, SKU o referencia (opcional)", 22),
        ("PRODUCTO", "Nombre del producto o servicio (Obligatorio)", 32),
        ("CATEGORIA", "carnes, abarrotes, lacteos, fruver, bebidas, aseo, servicios, otros", 22),
        ("CANTIDAD_PEDIDO", "Cantidad recibida que se SUMARÁ al stock actual", 18),
        ("UNIDAD_MEDIDA", "kg, lb, und, paquete, servicio", 16),
        ("COSTO_UNITARIO_COMPRA", "Precio de compra del proveedor ($ COP sin puntos)", 24),
        ("PRECIO_VENTA_PUBLICO", "Precio de venta al cliente ($ COP sin puntos)", 24),
        ("ES_SERVICIO", "Marcar SI si es servicio/flete sin stock, o NO", 15),
    ]

    ws.row_dimensions[4].height = 26
    for col_idx, (h_title, comment, width) in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col_idx, value=h_title)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
        cell.border = border_thin
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # Filas de ejemplo del mundo real comercial
    ejemplos = [
        ("770100100201", "Pechuga de Pollo Despresada", "carnes", 25.5, "kg", 13500, 18500, "NO"),
        ("770200200302", "Lomo de Res para Asar", "carnes", 18.0, "kg", 24000, 32000, "NO"),
        ("770300300403", "Arroz Diana 1000g", "abarrotes", 50.0, "und", 4100, 5200, "NO"),
        ("770400400504", "Queso Campesino Boyacense", "lacteos", 15.0, "kg", 14000, 19000, "NO"),
        ("770500500605", "Detergente Fab Polvo 1kg", "aseo", 30.0, "und", 6800, 8900, "NO"),
        ("SERV-001", "Servicio de Domicilio Urbano", "servicios", 0.0, "servicio", 0, 5000, "SI"),
    ]

    fill_ejemplo = PatternFill(start_color=verde_claro, end_color=verde_claro, fill_type="solid")
    font_ejemplo = Font(name="Calibri", size=10)

    for row_idx, data in enumerate(ejemplos, start=5):
        ws.row_dimensions[row_idx].height = 20
        for col_idx, val in enumerate(data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = font_ejemplo
            cell.border = border_thin
            if col_idx in (1, 3, 5, 8):
                cell.alignment = align_center
            elif col_idx in (4, 6, 7):
                cell.alignment = align_right
                if col_idx in (6, 7):
                    cell.number_format = "$#,##0"
                else:
                    cell.number_format = "#,##0.00"
            else:
                cell.alignment = align_left

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def procesar_archivo_pedido_excel(
    establecimiento: Establecimiento,
    archivo_bytes_o_file,
    usuario,
    registrar_compra: bool = False,
    proveedor: str = "Proveedor Pedido",
) -> Dict[str, Any]:
    """
    Lee y procesa un archivo Excel (.xlsx) con el pedido o remisión de mercancía.
    Reglas de negocio:
    1. Si el producto ya existe (coincidencia por código de barras o por nombre normalizado):
       - SUMA la cantidad_pedido a existencias actuales (jamás sobrescribe a la baja).
       - Actualiza precios de venta y costos si son mayores a cero.
    2. Si el producto no existe:
       - Lo crea en el catálogo con sus datos completos.
    3. Sincroniza con 'ItemPedido':
       - Si el producto estaba pendiente en lista de compras, se actualiza a 'comprado'.
    4. Opcional: Genera el registro de Compra contable consolidado para egresos del día.
    5. Registra auditoría con trazabilidad inmutable.
    """
    wb = openpyxl.load_workbook(archivo_bytes_o_file, data_only=True)
    ws = wb.active

    creados = 0
    actualizados = 0
    total_unidades = Decimal("0")
    total_costo_compra = Decimal("0")
    filas_procesadas = 0
    errores: List[str] = []

    # Buscar fila de encabezados (usualmente fila 4, o la primera que tenga 'PRODUCTO')
    header_row_idx = None
    col_map = {}

    for r in range(1, min(15, ws.max_row + 1)):
        row_vals = [str(ws.cell(row=r, column=c).value or "").strip().upper() for c in range(1, ws.max_column + 1)]
        if any("PRODUCTO" in v or "DESCRIPCION" in v or "NOMBRE" in v for v in row_vals):
            header_row_idx = r
            for c_idx, val in enumerate(row_vals, start=1):
                if "CODIGO" in val or "BARRA" in val or "SKU" in val or "EAN" in val:
                    col_map["codigo"] = c_idx
                elif "PRODUCTO" in val or "DESCRIPCION" in val or "NOMBRE" in val:
                    col_map["nombre"] = c_idx
                elif "CATEGORIA" in val:
                    col_map["categoria"] = c_idx
                elif "CANTIDAD" in val or "PEDIDO" in val or "STOCK" in val:
                    col_map["cantidad"] = c_idx
                elif "UNIDAD" in val or "MEDIDA" in val:
                    col_map["unidad"] = c_idx
                elif "COSTO" in val or "COMPRA" in val:
                    col_map["costo"] = c_idx
                elif "PRECIO" in val or "VENTA" in val:
                    col_map["precio"] = c_idx
                elif "SERVICIO" in val:
                    col_map["servicio"] = c_idx
            break

    if not header_row_idx or "nombre" not in col_map:
        # Fallback a columnas posicionales estándar
        col_map = {
            "codigo": 1,
            "nombre": 2,
            "categoria": 3,
            "cantidad": 4,
            "unidad": 5,
            "costo": 6,
            "precio": 7,
            "servicio": 8,
        }
        header_row_idx = 4

    categorias_validas = {c[0] for c in Producto.CATEGORIAS}

    with transaction.atomic():
        for r in range(header_row_idx + 1, ws.max_row + 1):
            raw_nombre = ws.cell(row=r, column=col_map.get("nombre", 2)).value
            if not raw_nombre:
                continue

            nombre = str(raw_nombre).strip()
            if not nombre or nombre.upper().startswith("INSTRUCCIONES") or nombre.upper().startswith("TOTAL"):
                continue

            raw_codigo = ws.cell(row=r, column=col_map.get("codigo", 1)).value
            codigo_barras = str(raw_codigo).strip() if raw_codigo is not None else ""
            if codigo_barras.endswith(".0"):
                codigo_barras = codigo_barras[:-2]

            raw_cat = str(ws.cell(row=r, column=col_map.get("categoria", 3)).value or "").strip().lower()
            categoria = raw_cat if raw_cat in categorias_validas else "otros"

            raw_cant = ws.cell(row=r, column=col_map.get("cantidad", 4)).value
            try:
                cantidad = Decimal(str(raw_cant or 0).replace(",", "."))
                if cantidad < 0:
                    cantidad = Decimal("0")
            except Exception:
                cantidad = Decimal("0")

            raw_unidad = str(ws.cell(row=r, column=col_map.get("unidad", 5)).value or "kg").strip().lower()
            if "libra" in raw_unidad or raw_unidad == "lb":
                unidad_medida = "lb"
            elif "un" in raw_unidad:
                unidad_medida = "und"
            elif "serv" in raw_unidad:
                unidad_medida = "servicio"
            else:
                unidad_medida = "kg"

            raw_costo = ws.cell(row=r, column=col_map.get("costo", 6)).value
            try:
                costo_unitario = Decimal(str(raw_costo or 0).replace(",", "."))
            except Exception:
                costo_unitario = Decimal("0")

            raw_precio = ws.cell(row=r, column=col_map.get("precio", 7)).value
            try:
                precio_venta = Decimal(str(raw_precio or 0).replace(",", "."))
            except Exception:
                precio_venta = Decimal("0")

            raw_serv = str(ws.cell(row=r, column=col_map.get("servicio", 8)).value or "").strip().upper()
            es_servicio = (raw_serv in ("SI", "S", "TRUE", "1") or categoria == "servicios")

            # Buscar producto existente en este establecimiento
            prod = None
            if codigo_barras:
                prod = Producto.objects.filter(establecimiento=establecimiento, codigo_barras=codigo_barras).first()

            if not prod:
                # Búsqueda por nombre exacto o insensible
                prod = Producto.objects.filter(establecimiento=establecimiento, nombre__iexact=nombre).first()

            if not prod:
                # Búsqueda por coincidencia normalizada
                nombre_norm = normalizar_texto(nombre)
                for p in Producto.objects.filter(establecimiento=establecimiento):
                    if normalizar_texto(p.nombre) == nombre_norm:
                        prod = p
                        break

            if prod:
                # SUMAR STOCK (Regla estricta: un pedido o remisión siempre SUMA existencias)
                prod.stock_kilos += cantidad
                if precio_venta > 0:
                    prod.precio_kilo = precio_venta
                if costo_unitario > 0:
                    prod.costo_unitario = costo_unitario
                if codigo_barras and not prod.codigo_barras:
                    prod.codigo_barras = codigo_barras
                if categoria != "otros" and prod.categoria == "otros":
                    prod.categoria = categoria
                prod.save()
                actualizados += 1
            else:
                # CREAR NUEVO PRODUCTO
                prod = Producto.objects.create(
                    establecimiento=establecimiento,
                    categoria=categoria,
                    nombre=nombre,
                    codigo_barras=codigo_barras,
                    es_servicio=es_servicio,
                    unidad_medida=unidad_medida,
                    costo_unitario=costo_unitario,
                    stock_kilos=cantidad,
                    precio_kilo=precio_venta if precio_venta > 0 else Decimal("1000"),
                    estado="activo",
                )
                creados += 1

            # Desmarcar de lista de compras / faltantes si estaba pendiente
            ItemPedido.objects.filter(
                establecimiento=establecimiento,
                producto=prod,
                estado="pendiente"
            ).update(estado="comprado")
            ItemPedido.objects.filter(
                establecimiento=establecimiento,
                nombre_producto__iexact=prod.nombre,
                estado="pendiente"
            ).update(estado="comprado")

            total_unidades += cantidad
            total_costo_compra += (cantidad * costo_unitario)
            filas_procesadas += 1

        # Registrar compra consolidada si se solicitó y hubo costo
        compra_creada = None
        if registrar_compra and total_costo_compra > 0:
            compra_creada = Compra.objects.create(
                establecimiento=establecimiento,
                usuario=usuario,
                fecha=timezone.localdate(),
                valor=total_costo_compra,
                proveedor=proveedor or "Proveedor Pedido Excel",
                concepto=f"Pedido e Ingreso de Inventario Excel ({filas_procesadas} items)",
                estado="vigente",
            )

        # Registro de Auditoría
        Auditoria.objects.create(
            usuario=usuario,
            entidad_afectada="inventario",
            id_registro=establecimiento.pk,
            accion="importar_excel",
            valor_nuevo=f"Importados: {filas_procesadas} items. Creados: {creados}, Actualizados: {actualizados}, Unidades: {total_unidades}, Costo: ${total_costo_compra}",
            motivo=f"Carga de archivo de pedido Excel por {usuario.username}",
        )

    return {
        "filas_procesadas": filas_procesadas,
        "creados": creados,
        "actualizados": actualizados,
        "total_unidades": total_unidades,
        "total_costo_compra": total_costo_compra,
        "compra_creada": compra_creada,
        "errores": errores,
    }


def generar_excel_conciliacion_pagos(establecimiento: Establecimiento, transacciones: List[Dict[str, Any]]) -> bytes:
    """
    Genera un informe en Excel profesional para la conciliación de Pagos Electrónicos
    (Bre-B, Nequi, Daviplata, Transferencias) para entrega al contador y cruce con extractos bancarios.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Conciliación Pagos Electrónicos"

    azul_header = "0F172A"
    gris_borde = "CBD5E1"
    verde_ok = "10B981"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(start_color=azul_header, end_color=azul_header, fill_type="solid")
    border_thin = Border(
        left=Side(style="thin", color=gris_borde),
        right=Side(style="thin", color=gris_borde),
        top=Side(style="thin", color=gris_borde),
        bottom=Side(style="thin", color=gris_borde),
    )
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    # Título
    ws.merge_cells("A1:J1")
    t1 = ws["A1"]
    t1.value = f"INFORME DE CONCILIACIÓN DE PAGOS ELECTRÓNICOS • {establecimiento.nombre.upper()}"
    t1.font = Font(name="Calibri", size=13, bold=True, color="FFFFFF")
    t1.fill = fill_header
    t1.alignment = align_center
    ws.row_dimensions[1].height = 26

    ws.merge_cells("A2:J2")
    t2 = ws["A2"]
    t2.value = f"NIT: {establecimiento.nit or 'RUT Simplificado'} | Municipio: {establecimiento.municipio or 'Tunja'} | Generado: {timezone.localtime().strftime('%d/%m/%Y %H:%M')}"
    t2.font = Font(name="Calibri", size=9, italic=True)
    t2.alignment = align_center

    headers = [
        ("FECHA Y HORA", 18),
        ("CANAL / MEDIO", 16),
        ("REFERENCIA ÚNICA", 24),
        ("TOKEN MOSTRADOR", 16),
        ("MONTO ($ COP)", 16),
        ("BANCO ORIGEN / DESTINO", 24),
        ("ID RIEL BANREP / COMPROBANTE", 28),
        ("CAJERO RESPONSABLE", 20),
        ("ESTADO DE PAGO", 18),
        ("CONCILIADO EXTRACTO", 18),
    ]

    ws.row_dimensions[4].height = 24
    for c_idx, (h_name, width) in enumerate(headers, start=1):
        c = ws.cell(row=4, column=c_idx, value=h_name)
        c.font = font_header
        c.fill = fill_header
        c.alignment = align_center
        c.border = border_thin
        ws.column_dimensions[get_column_letter(c_idx)].width = width

    font_data = Font(name="Calibri", size=10)

    for r_idx, tx in enumerate(transacciones, start=5):
        ws.row_dimensions[r_idx].height = 20
        row_vals = [
            tx.get("fecha_hora"),
            tx.get("medio_display"),
            tx.get("referencia"),
            tx.get("token_corto", "-"),
            float(tx.get("monto", 0)),
            tx.get("banco", "-"),
            tx.get("id_riel", "-"),
            tx.get("cajero", "-"),
            tx.get("estado_display"),
            "SI (Verificado)" if tx.get("conciliado") else "Pendiente",
        ]
        for c_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.font = font_data
            cell.border = border_thin
            if c_idx in (1, 2, 4, 9, 10):
                cell.alignment = align_center
            elif c_idx == 5:
                cell.alignment = align_right
                cell.number_format = "$#,##0"
            else:
                cell.alignment = align_left

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
