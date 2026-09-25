"""
registro/tax_engine.py
Motor Tributario Paramétrico Multi-Municipio (ICA, Avisos y Tableros, Sobretasa Bomberil)
y Base de Integración para Medios Magnéticos Municipales (Exógena Municipal).

Diseñado específicamente para el Estatuto Tributario de Tunja (Acuerdo 0032 de 2020)
y extensible a cualquier municipio de Colombia mediante la tabla ReglaTributariaMunicipio.
"""
from decimal import Decimal
from typing import Dict, Any, List, Optional
from datetime import date
from django.utils import timezone
from django.db.models import Sum

from .models import (
    Establecimiento,
    Municipio,
    ReglaTributariaMunicipio,
    Venta,
    Compra,
    Retencion,
    RegistroExogenaMunicipal,
)


def obtener_o_crear_regla_municipio(municipio: Municipio) -> ReglaTributariaMunicipio:
    """Obtiene la regla tributaria del municipio o crea la estándar (15% Avisos, 5% Bomberos)."""
    regla, _ = ReglaTributariaMunicipio.objects.get_or_create(
        municipio=municipio,
        defaults={
            "porcentaje_avisos_y_tableros": Decimal("15.00"),
            "porcentaje_sobretasa_bomberil": Decimal("5.00"),
            "base_minima_declarante_uvt": Decimal("0.00"),
            "meses_periodo_ica": 2,
            "acuerdo_municipal_referencia": "Estatuto Tributario Municipal (Acuerdo 0032/2020)",
        },
    )
    return regla


def liquidar_declaracion_sugerida_ica(
    establecimiento: Establecimiento,
    fecha_inicio: date,
    fecha_fin: date,
    ingresos_fuera_municipio: Decimal = Decimal("0"),
    devoluciones_descuentos: Decimal = Decimal("0"),
) -> Dict[str, Any]:
    """
    Calcula la Declaración Sugerida de ICA para el periodo dado.
    Sigue estrictamente la estructura del formulario oficial de la Secretaría de Hacienda:
      1. Ingresos brutos del periodo.
      2. Menos deducciones (territorialidad, devoluciones).
      3. Base gravable neta.
      4. Impuesto de Industria y Comercio según tarifas CIIU.
      5. Impuesto de Avisos y Tableros (15%).
      6. Sobretasa Bomberil (5%).
      7. Menos retenciones a favor (ReteICA).
      8. Saldo neto a pagar.
    """
    municipio = establecimiento.municipio
    if not municipio:
        # Fallback municipio por defecto Tunja
        municipio, _ = Municipio.objects.get_or_create(
            codigo_dane="15001", defaults={"nombre": "Tunja", "departamento": "Boyacá"}
        )

    regla = obtener_o_crear_regla_municipio(municipio)

    # 1. Ingresos Brutos del periodo (Ventas vigentes)
    ventas = Venta.objects.filter(
        establecimiento=establecimiento,
        fecha__range=(fecha_inicio, fecha_fin),
        estado="vigente",
    )
    ingresos_brutos = ventas.aggregate(total=Sum("valor"))["total"] or Decimal("0")

    # 2. Depuración de Base Gravable
    total_deducciones = (ingresos_fuera_municipio or Decimal("0")) + (devoluciones_descuentos or Decimal("0"))
    base_gravable_neta = max(Decimal("0"), ingresos_brutos - total_deducciones)

    # 3. Liquidación por Actividad Económica (CIIU)
    actividades = establecimiento.actividades.all()
    desglose_actividades = []
    impuesto_neto_ica = Decimal("0")

    if actividades.exists():
        # Distribuir base gravable proporcionalmente o por actividad registrada
        for act in actividades:
            ventas_act = ventas.filter(actividad=act)
            subtotal_act = ventas_act.aggregate(total=Sum("valor"))["total"] or Decimal("0")
            if subtotal_act > 0:
                impuesto_act = (subtotal_act * act.tarifa_x_mil) / Decimal("1000")
                impuesto_neto_ica += impuesto_act
                desglose_actividades.append({
                    "codigo_ciiu": act.codigo,
                    "descripcion": act.descripcion,
                    "tarifa_x_mil": act.tarifa_x_mil,
                    "ingresos_gravados": subtotal_act,
                    "impuesto_liquidado": impuesto_act.quantize(Decimal("1")),
                })

    # Si no hubo actividades discriminadas o hubo remanente general
    if impuesto_neto_ica == Decimal("0") and base_gravable_neta > Decimal("0"):
        tarifa_defecto = Decimal("6.0") # Tarifa común comercio al por menor (6 por mil)
        impuesto_neto_ica = (base_gravable_neta * tarifa_defecto) / Decimal("1000")
        desglose_actividades.append({
            "codigo_ciiu": establecimiento.actividad_economica or "4711",
            "descripcion": "Comercio al por menor",
            "tarifa_x_mil": tarifa_defecto,
            "ingresos_gravados": base_gravable_neta,
            "impuesto_liquidado": impuesto_neto_ica.quantize(Decimal("1")),
        })

    impuesto_neto_ica = impuesto_neto_ica.quantize(Decimal("1"))

    # 4. Impuestos Complementarios
    factor_avisos = regla.porcentaje_avisos_y_tableros / Decimal("100")
    factor_bomberos = regla.porcentaje_sobretasa_bomberil / Decimal("100")

    impuesto_avisos_tableros = (impuesto_neto_ica * factor_avisos).quantize(Decimal("1"))
    sobretasa_bomberil = (impuesto_neto_ica * factor_bomberos).quantize(Decimal("1"))

    # Sobretasa de Seguridad (Ley 1421 de 2011 / Acuerdo Municipal)
    sobretasa_seguridad = Decimal("0")

    total_impuesto_a_cargo = impuesto_neto_ica + impuesto_avisos_tableros + sobretasa_bomberil + sobretasa_seguridad

    # 5. Menos Retenciones de ICA que le practicaron clientes al negocio (ReteICA a favor)
    retenciones_favor = Retencion.objects.filter(
        establecimiento=establecimiento,
        fecha__range=(fecha_inicio, fecha_fin),
        tipo="ica",
        estado="vigente",
    ).aggregate(total=Sum("valor"))["total"] or Decimal("0")

    # Mapeo oficial Formulario Único Nacional (Formulario 02 - Tunja)
    # Renglón 8: Total ingresos en todo el país (Consolidación DIAN)
    # Renglón 9: Menos ingresos obtenidos fuera de este municipio
    if getattr(establecimiento, "declara_renta_dian", False):
        propietario = getattr(establecimiento, "propietario_creador", None)
        ingresos_otras_sedes = Decimal("0")
        if propietario:
            from .models import Establecimiento as EstModel
            otras_sedes = EstModel.objects.filter(propietario_creador=propietario).exclude(pk=establecimiento.pk)
            for s in otras_sedes:
                vt = Venta.objects.filter(establecimiento=s, fecha__range=(fecha_inicio, fecha_fin), estado="vigente")
                subt = vt.aggregate(total=Sum("valor"))["total"] or Decimal("0")
                ingresos_otras_sedes += subt

        otros_fuera = (getattr(establecimiento, "otros_ingresos_nacionales_anual", Decimal("0")) or Decimal("0")) + (ingresos_fuera_municipio or Decimal("0"))
        total_fuera = ingresos_otras_sedes + otros_fuera
        r8_ingresos_pais = ingresos_brutos + total_fuera
        r9_ingresos_fuera = total_fuera
    else:
        r8_ingresos_pais = ingresos_brutos + (ingresos_fuera_municipio or Decimal("0"))
        r9_ingresos_fuera = ingresos_fuera_municipio or Decimal("0")

    r10_ingresos_municipio = max(Decimal("0"), r8_ingresos_pais - r9_ingresos_fuera)
    r11_devoluciones = devoluciones_descuentos or Decimal("0")
    r12_exportaciones = Decimal("0")
    r13_venta_activos = Decimal("0")
    r14_no_gravados = Decimal("0")
    r15_exentas = Decimal("0")
    r16_total_ingresos_gravables = max(
        Decimal("0"),
        r10_ingresos_municipio - r11_devoluciones - r12_exportaciones - r13_venta_activos - r14_no_gravados - r15_exentas
    )

    r17_total_impuesto_gravado = impuesto_neto_ica
    r18_capacidad_kw = 0
    r19_impuesto_ley_56 = Decimal("0")
    r20_impuesto_ica = r17_total_impuesto_gravado + r19_impuesto_ley_56
    r21_avisos_tableros = impuesto_avisos_tableros
    r22_sector_financiero = Decimal("0")
    r23_sobretasa_bomberil = sobretasa_bomberil
    r24_sobretasa_seguridad = sobretasa_seguridad
    r25_total_impuesto_cargo = total_impuesto_a_cargo

    r26_exenciones = Decimal("0")
    r27_retenciones_favor = retenciones_favor
    r28_autorretenciones = Decimal("0")
    r29_anticipo_anterior = getattr(establecimiento, "anticipo_ano_anterior", Decimal("0")) or Decimal("0")
    r30_anticipo_siguiente = Decimal("0")
    r31_sanciones = Decimal("0")
    r32_saldo_favor_anterior = getattr(establecimiento, "saldo_favor_anterior", Decimal("0")) or Decimal("0")

    subtotal_cargo = (
        r25_total_impuesto_cargo - r26_exenciones - r27_retenciones_favor
        - r28_autorretenciones - r29_anticipo_anterior + r30_anticipo_siguiente
        + r31_sanciones - r32_saldo_favor_anterior
    )

    if subtotal_cargo >= 0:
        r33_saldo_cargo = subtotal_cargo
        r34_saldo_favor = Decimal("0")
    else:
        r33_saldo_cargo = Decimal("0")
        r34_saldo_favor = abs(subtotal_cargo)

    r35_valor_pagar = r33_saldo_cargo
    r36_descuento_pronto_pago = Decimal("0")
    r37_intereses_mora = Decimal("0")
    r38_total_pagar = max(Decimal("0"), r35_valor_pagar - r36_descuento_pronto_pago + r37_intereses_mora)
    r39_pago_voluntario = Decimal("0")
    r40_total_con_pago_voluntario = r38_total_pagar + r39_pago_voluntario

    # Dígito de verificación para el NIT
    nit_digitos = "".join(c for c in (establecimiento.nit or "") if c.isdigit())
    dv = "0"
    if nit_digitos:
        pesos = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]
        suma = sum(int(c) * pesos[i] for i, c in enumerate(reversed(nit_digitos)) if i < len(pesos))
        res = suma % 11
        dv = str(11 - res) if res > 1 else str(res)

    # Organización de actividades C para las 3 casillas oficiales
    act1 = desglose_actividades[0] if len(desglose_actividades) > 0 else None
    act2 = desglose_actividades[1] if len(desglose_actividades) > 1 else None
    act3 = desglose_actividades[2] if len(desglose_actividades) > 2 else None
    otras_act = desglose_actividades[3:] if len(desglose_actividades) > 3 else []

    ano_grav = fecha_fin.year
    num_form = f"2602{establecimiento.id:04d}{ano_grav}"

    return {
        "establecimiento": establecimiento.nombre,
        "nit": establecimiento.nit,
        "municipio": f"{municipio.nombre} ({municipio.codigo_dane})",
        "normativa": regla.acuerdo_municipal_referencia,
        "periodo": {
            "fecha_inicio": fecha_inicio.strftime("%d/%m/%Y"),
            "fecha_fin": fecha_fin.strftime("%d/%m/%Y"),
            "ano_gravable": ano_grav,
            "fecha_maxima": f"29/05/{ano_grav + 1}",
        },
        "formulario_numero": num_form,
        "contribuyente": {
            "nombre": establecimiento.nombre,
            "tipo_doc": "NIT" if len(nit_digitos) > 8 else "C.C.",
            "documento": establecimiento.nit or "0",
            "dv": dv,
            "direccion": establecimiento.direccion or "CRA 10 # 15-20 CENTRO",
            "departamento": municipio.departamento.upper() if municipio else "BOYACÁ",
            "municipio_nombre": municipio.nombre.upper() if municipio else "TUNJA",
            "telefono": getattr(establecimiento, "llave_bre_b", "") or "3103083452",
            "correo": establecimiento.correo_reportes or "comercio@diariocomercial.co",
            "no_establecimientos": 1,
            "clasificacion": "COMÚN",
            "matricula_mercantil": f"TUN-{establecimiento.id:06d}",
        },
        "actividades_c": {
            "actividad_1": act1,
            "actividad_2": act2,
            "actividad_3": act3,
            "otras": otras_act,
            "total_ingresos": r16_total_ingresos_gravables,
            "total_impuesto": r17_total_impuesto_gravado,
        },
        "renglones": {
            # Numeración oficial Formulario 02 (Tunja)
            "8_total_ingresos_pais": r8_ingresos_pais,
            "9_ingresos_fuera_municipio": r9_ingresos_fuera,
            "10_total_ingresos_municipio": r10_ingresos_municipio,
            "11_devoluciones_descuentos": r11_devoluciones,
            "12_exportaciones": r12_exportaciones,
            "13_venta_activos_fijos": r13_venta_activos,
            "14_no_gravados_excluidos": r14_no_gravados,
            "15_exentas_municipio": r15_exentas,
            "16_total_ingresos_gravables": r16_total_ingresos_gravables,
            "17_total_impuesto_gravado": r17_total_impuesto_gravado,
            "18_generacion_energia": r18_capacidad_kw,
            "19_impuesto_ley_56": r19_impuesto_ley_56,
            "20_impuesto_industria_comercio": r20_impuesto_ica,
            "21_impuesto_avisos_tableros": r21_avisos_tableros,
            "22_pago_unidades_financiero": r22_sector_financiero,
            "23_sobretasa_bomberil": r23_sobretasa_bomberil,
            "24_sobretasa_seguridad": r24_sobretasa_seguridad,
            "25_total_impuesto_a_cargo": r25_total_impuesto_cargo,
            "26_exenciones_impuesto": r26_exenciones,
            "27_menos_retenciones_ica_favor": r27_retenciones_favor,
            "28_menos_autorretenciones": r28_autorretenciones,
            "29_menos_anticipo_anterior": r29_anticipo_anterior,
            "30_anticipo_siguiente": r30_anticipo_siguiente,
            "31_sanciones": r31_sanciones,
            "32_menos_saldo_favor_anterior": r32_saldo_favor_anterior,
            "33_total_saldo_a_cargo": r33_saldo_cargo,
            "34_total_saldo_a_favor": r34_saldo_favor,
            "35_valor_a_pagar": r35_valor_pagar,
            "36_descuento_pronto_pago": r36_descuento_pronto_pago,
            "37_intereses_mora": r37_intereses_mora,
            "38_total_a_pagar": r38_total_pagar,
            "39_pago_voluntario": r39_pago_voluntario,
            "40_total_con_pago_voluntario": r40_total_con_pago_voluntario,

            # Compatibilidad hacia atrás con tests existentes
            "1_ingresos_brutos": r8_ingresos_pais,
            "2_ingresos_fuera_municipio": r9_ingresos_fuera,
            "3_devoluciones_descuentos": r11_devoluciones,
            "4_base_gravable_neta": r16_total_ingresos_gravables,
            "5_impuesto_neto_ica": r20_impuesto_ica,
            "6_impuesto_avisos_tableros_15pct": r21_avisos_tableros,
            "7_sobretasa_bomberil": r23_sobretasa_bomberil,
            "8_total_impuesto_a_cargo": r25_total_impuesto_cargo,
            "9_menos_retenciones_ica_a_favor": r27_retenciones_favor,
            "10_total_saldo_a_pagar": r33_saldo_cargo,
        },
        "desglose_ciiu": desglose_actividades,
    }


# ============================================================================
# MEDIOS MAGNÉTICOS MUNICIPALES (EXÓGENA MUNICIPAL)
# ============================================================================

def registrar_evento_exogena(
    establecimiento: Establecimiento,
    tipo_registro: str,
    nit_tercero: str,
    nombre_razon_social: str,
    monto_base: Decimal,
    monto_impuesto_retencion: Decimal = Decimal("0"),
    tarifa_aplicada: Decimal = Decimal("0"),
    direccion_tercero: str = "",
    fecha_transaccion: Optional[date] = None,
) -> RegistroExogenaMunicipal:
    """
    Almacena cada operación sujeta a reporte en Medios Magnéticos Municipales.
    Permite generar al final del año gravable el archivo plano oficial de la Alcaldía.
    """
    fecha = fecha_transaccion or timezone.localdate()
    municipio = establecimiento.municipio
    if not municipio:
        municipio, _ = Municipio.objects.get_or_create(codigo_dane="15001", defaults={"nombre": "Tunja", "departamento": "Boyacá"})

    return RegistroExogenaMunicipal.objects.create(
        establecimiento=establecimiento,
        municipio=municipio,
        año_gravable=fecha.year,
        tipo_registro=tipo_registro,
        nit_tercero=nit_tercero.strip(),
        nombre_razon_social=nombre_razon_social.strip()[:160],
        direccion_tercero=direccion_tercero.strip()[:160],
        monto_base=monto_base,
        monto_impuesto_retencion=monto_impuesto_retencion,
        tarifa_aplicada=tarifa_aplicada,
        fecha_transaccion=fecha,
    )


def generar_resumen_exogena_anual(establecimiento: Establecimiento, año: int) -> Dict[str, Any]:
    """Genera la consolidación anual para el formato de exógena municipal exigido por la Alcaldía."""
    registros = RegistroExogenaMunicipal.objects.filter(
        establecimiento=establecimiento, año_gravable=año
    )
    compras = registros.filter(tipo_registro="compra")
    ventas_terceros = registros.filter(tipo_registro="venta")
    rete_practicadas = registros.filter(tipo_registro="reteica_practicado")
    rete_asumidas = registros.filter(tipo_registro="reteica_asumido")

    return {
        "establecimiento": establecimiento.nombre,
        "año_gravable": año,
        "municipio": establecimiento.municipio.nombre if establecimiento.municipio else "Tunja",
        "resumen": {
            "total_compras_reportadas": compras.aggregate(s=Sum("monto_base"))["s"] or Decimal("0"),
            "total_ventas_reportadas": ventas_terceros.aggregate(s=Sum("monto_base"))["s"] or Decimal("0"),
            "total_reteica_practicado": rete_practicadas.aggregate(s=Sum("monto_impuesto_retencion"))["s"] or Decimal("0"),
            "total_reteica_asumido": rete_asumidas.aggregate(s=Sum("monto_impuesto_retencion"))["s"] or Decimal("0"),
        },
        "conteo_terceros_unicos": registros.values("nit_tercero").distinct().count(),
    }


# ============================================================================
# EXÓGENA NACIONAL DIAN - FORMATO 1007 (INGRESOS PROPIOS RECIBIDOS)
# ============================================================================

def obtener_o_crear_consumidor_final(establecimiento):
    """
    Retorna o inicializa el registro normativo DIAN de Consumidor Final para ventas generales de mostrador.
    Normativa DIAN (Resolución 000042 de 2020 y Resolución 000165 de 2023):
    Documento: 222222222222 (12 dígitos en Factura Electrónica UBL 2.1 / 222222222 en Exógena 1007).
    Nombre: CONSUMIDOR FINAL.
    """
    from .models import Cliente
    cliente, _ = Cliente.objects.get_or_create(
        establecimiento=establecimiento,
        nit_cedula="222222222222",
        defaults={
            "nombre": "CONSUMIDOR FINAL",
            "tipo_documento": "13",
            "tipo_persona": "natural",
            "regimen_fiscal": "no_responsable_iva",
            "correo_electronico": "consumidorfinal@dian.gov.co",
            "direccion": "VENTA DE MOSTRADOR",
            "municipio_nombre": "Tunja",
            "departamento_nombre": "Boyacá",
            "telefono": "0000000000",
            "es_consumidor_final": True,
        },
    )
    return cliente


def liquidar_exogena_dian_formato_1007(establecimiento, año: int) -> Dict[str, Any]:
    """
    Genera la liquidación y discriminación para la Información Exógena DIAN - Formato 1007.
    
    Regla Tributaria Normativa DIAN:
    1. Clientes que solicitaron FACTURA ELECTRÓNICA individual:
       Se reportan uno a uno con su Cédula o NIT real, razón social, correo, dirección,
       y la suma total de sus compras del año.
    2. Clientes Generales de MOSTRADOR (Consumidor Final / Cuantías Menores):
       NO se reportan individualmente. Se SUMAN todas las compras de mostrador bajo el
       pseudonit normativo '222222222' (9 doses según la DIAN para Exógena), con razón
       social 'CUANTÍAS MENORES - CONSUMIDOR FINAL'.
    """
    from django.db.models import Sum
    from .models import Venta

    ventas_ano = Venta.objects.filter(
        establecimiento=establecimiento,
        fecha__year=año,
        estado="vigente",
    ).select_related("cliente")

    total_ingresos_brutos = ventas_ano.aggregate(s=Sum("valor"))["s"] or Decimal("0")

    total_mostrador_consumidor_final = Decimal("0")
    total_clientes_individuales = Decimal("0")
    conteo_facturas_individuales = 0
    conteo_tickets_mostrador = 0

    ventas_por_cliente = {}

    for v in ventas_ano:
        cli = v.cliente
        es_mostrador = (
            cli is None
            or cli.es_consumidor_final
            or cli.nit_cedula.strip() in ("222222222222", "222222222")
            or not v.solicita_factura_electronica
        )

        if es_mostrador:
            total_mostrador_consumidor_final += v.valor
            conteo_tickets_mostrador += 1
        else:
            conteo_facturas_individuales += 1
            total_clientes_individuales += v.valor
            key = (cli.nit_cedula.strip(), cli.pk)
            if key not in ventas_por_cliente:
                ventas_por_cliente[key] = {
                    "cliente": cli,
                    "nit": cli.nit_cedula.strip(),
                    "dv": cli.dv or "",
                    "tipo_doc": cli.tipo_documento or "13",
                    "tipo_doc_display": cli.get_tipo_documento_display() if hasattr(cli, "get_tipo_documento_display") else "CC",
                    "nombre": cli.nombre,
                    "direccion": cli.direccion or "Tunja, Boyacá",
                    "correo": cli.correo_electronico or "",
                    "telefono": cli.telefono or "",
                    "regimen": cli.get_regimen_fiscal_display() if hasattr(cli, "get_regimen_fiscal_display") else "No responsable IVA",
                    "municipio": cli.municipio_nombre or (establecimiento.municipio.nombre if establecimiento.municipio else "Tunja"),
                    "departamento": cli.departamento_nombre or "Boyacá",
                    "total_compras": Decimal("0"),
                    "conteo_facturas": 0,
                    "cantidad_facturas": 0,
                }
            ventas_por_cliente[key]["total_compras"] += v.valor
            ventas_por_cliente[key]["conteo_facturas"] += 1
            ventas_por_cliente[key]["cantidad_facturas"] += 1

    lista_individuales = sorted(ventas_por_cliente.values(), key=lambda x: x["total_compras"], reverse=True)

    mun_nombre = establecimiento.municipio.nombre if establecimiento.municipio else "Tunja"
    dep_nombre = establecimiento.municipio.departamento if establecimiento.municipio else "Boyacá"
    dane_mun = establecimiento.municipio.codigo_dane[-3:] if establecimiento.municipio else "001"
    dane_dep = establecimiento.municipio.codigo_dane[:2] if establecimiento.municipio else "15"

    registro_consumidor_final_1007 = {
        "concepto": "4001",
        "tipo_doc": "43",  # En prevalidador DIAN Formato 1007 cuantías menores se reporta 43 o 13
        "nit": "222222222",  # 9 doses reglamentario DIAN exógena
        "nit_factura_ubl": "222222222222",  # 12 doses en factura electrónica UBL
        "dv": "",
        "nombre": "CUANTÍAS MENORES - CONSUMIDOR FINAL (MOSTRADOR)",
        "pais": "169",
        "departamento_codigo": dane_dep,
        "municipio_codigo": dane_mun,
        "departamento": dep_nombre,
        "municipio": mun_nombre,
        "ingresos_brutos": total_mostrador_consumidor_final,
        "devoluciones": Decimal("0"),
        "ingreso_neto": total_mostrador_consumidor_final,
        "conteo_operaciones": conteo_tickets_mostrador,
        "es_consolidado_mostrador": True,
    }

    total_reportado_exogena = total_mostrador_consumidor_final + total_clientes_individuales

    return {
        "establecimiento": establecimiento,
        "año_gravable": año,
        "total_ingresos_brutos": total_ingresos_brutos,
        "total_mostrador_consumidor_final": total_mostrador_consumidor_final,
        "total_clientes_individuales": total_clientes_individuales,
        "total_reportado_exogena": total_reportado_exogena,
        "conteo_clientes_individuales": len(lista_individuales),
        "conteo_facturas_individuales": conteo_facturas_individuales,
        "conteo_tickets_mostrador": conteo_tickets_mostrador,
        "conteo_ventas_mostrador": conteo_tickets_mostrador,
        "registro_consolidado_mostrador": registro_consumidor_final_1007,
        "clientes_individuales": lista_individuales,
        "cuadre_perfecto": total_ingresos_brutos == total_reportado_exogena,
        "cuadre_exacto": total_ingresos_brutos == total_reportado_exogena,
        "diferencia_cuadre": abs(total_ingresos_brutos - total_reportado_exogena),
    }


def generar_excel_exogena_formato_1007(establecimiento, año: int) -> bytes:
    """Genera archivo Excel (.xlsx) estandarizado con la estructura del Formato 1007 de la DIAN."""
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    datos = liquidar_exogena_dian_formato_1007(establecimiento, año)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"DIAN_Formato_1007_{año}"

    fill_header = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    fill_mostrador = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
    fill_total = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    font_header = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_bold = Font(name="Calibri", size=10, bold=True)
    align_center = Alignment(horizontal="center", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1'),
    )

    # Título
    ws.merge_cells("A1:L1")
    ws["A1"] = f"DIRECCIÓN DE IMPUESTOS Y ADUANAS NACIONALES (DIAN) — INFORMACIÓN EXÓGENA FORMATO 1007 (INGRESOS)"
    ws["A1"].font = Font(name="Calibri", size=13, bold=True, color="1E3A8A")
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center")

    ws.merge_cells("A2:L2")
    ws["A2"] = f"Comercio: {establecimiento.nombre} | NIT: {establecimiento.nit} | Año Gravable: {año} | Total Ingresos: ${datos['total_ingresos_brutos']:,.0f} COP"
    ws["A2"].font = Font(name="Calibri", size=10, italic=True)

    headers = [
        "Concepto",
        "Tipo Doc",
        "Número Identificación",
        "DV",
        "Primer Apellido / Razón Social",
        "Correo Electrónico",
        "Dirección",
        "Depto",
        "Munc",
        "Ingresos Brutos Recibidos ($ COP)",
        "Devoluciones ($)",
        "Ingreso Neto Fiscal ($)",
    ]

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
        cell.border = thin_border

    # Fila 5: Consolidado de Mostrador / Consumidor Final (Suma de compras de clientes no identificados)
    r_mostrador = datos["registro_consolidado_mostrador"]
    row_num = 5

    fila_cf = [
        r_mostrador["concepto"],
        r_mostrador["tipo_doc"],
        r_mostrador["nit"],
        r_mostrador["dv"],
        r_mostrador["nombre"],
        "consumidorfinal@dian.gov.co",
        "VENTA DE MOSTRADOR",
        r_mostrador["departamento_codigo"],
        r_mostrador["municipio_codigo"],
        float(r_mostrador["ingresos_brutos"]),
        0.0,
        float(r_mostrador["ingreso_neto"]),
    ]

    for col_idx, val in enumerate(fila_cf, 1):
        cell = ws.cell(row=row_num, column=col_idx, value=val)
        cell.fill = fill_mostrador
        cell.border = thin_border
        if col_idx in (10, 11, 12):
            cell.number_format = "$#,##0"
            cell.alignment = align_right
        elif col_idx in (1, 2, 4, 8, 9):
            cell.alignment = align_center

    # Filas siguientes: Clientes individuales con Factura Electrónica
    row_num += 1
    dep_cod = establecimiento.municipio.codigo_dane[:2] if establecimiento.municipio else "15"
    mun_cod = establecimiento.municipio.codigo_dane[-3:] if establecimiento.municipio else "001"

    for cli in datos["clientes_individuales"]:
        fila_cli = [
            "4001",
            cli["tipo_doc"],
            cli["nit"],
            cli["dv"],
            cli["nombre"].upper(),
            cli["correo"],
            cli["direccion"],
            dep_cod,
            mun_cod,
            float(cli["total_compras"]),
            0.0,
            float(cli["total_compras"]),
        ]
        for col_idx, val in enumerate(fila_cli, 1):
            cell = ws.cell(row=row_num, column=col_idx, value=val)
            cell.border = thin_border
            if col_idx in (10, 11, 12):
                cell.number_format = "$#,##0"
                cell.alignment = align_right
            elif col_idx in (1, 2, 4, 8, 9):
                cell.alignment = align_center
        row_num += 1

    # Fila de Totales
    ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=9)
    total_label = ws.cell(row=row_num, column=1, value="TOTAL GENERAL DECLARADO EXÓGENA FORMATO 1007")
    total_label.font = font_bold
    total_label.alignment = align_right
    total_label.fill = fill_total
    total_label.border = thin_border

    tot_bruto = ws.cell(row=row_num, column=10, value=float(datos["total_reportado_exogena"]))
    tot_bruto.font = font_bold
    tot_bruto.number_format = "$#,##0"
    tot_bruto.alignment = align_right
    tot_bruto.fill = fill_total
    tot_bruto.border = thin_border

    tot_dev = ws.cell(row=row_num, column=11, value=0.0)
    tot_dev.font = font_bold
    tot_dev.number_format = "$#,##0"
    tot_dev.alignment = align_right
    tot_dev.fill = fill_total
    tot_dev.border = thin_border

    tot_neto = ws.cell(row=row_num, column=12, value=float(datos["total_reportado_exogena"]))
    tot_neto.font = font_bold
    tot_neto.number_format = "$#,##0"
    tot_neto.alignment = align_right
    tot_neto.fill = fill_total
    tot_neto.border = thin_border

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
