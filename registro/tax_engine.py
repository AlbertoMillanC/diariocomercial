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
    r8_ingresos_pais = ingresos_brutos
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
    r29_anticipo_anterior = Decimal("0")
    r30_anticipo_siguiente = Decimal("0")
    r31_sanciones = Decimal("0")
    r32_saldo_favor_anterior = Decimal("0")

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
