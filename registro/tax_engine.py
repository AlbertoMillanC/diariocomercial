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

    total_impuesto_a_cargo = impuesto_neto_ica + impuesto_avisos_tableros + sobretasa_bomberil

    # 5. Menos Retenciones de ICA que le practicaron clientes al negocio (ReteICA a favor)
    retenciones_favor = Retencion.objects.filter(
        establecimiento=establecimiento,
        fecha__range=(fecha_inicio, fecha_fin),
        tipo="ica",
        estado="vigente",
    ).aggregate(total=Sum("valor"))["total"] or Decimal("0")

    saldo_neto_a_pagar = max(Decimal("0"), total_impuesto_a_cargo - retenciones_favor)

    return {
        "establecimiento": establecimiento.nombre,
        "nit": establecimiento.nit,
        "municipio": f"{municipio.nombre} ({municipio.codigo_dane})",
        "normativa": regla.acuerdo_municipal_referencia,
        "periodo": {
            "fecha_inicio": fecha_inicio.strftime("%d/%m/%Y"),
            "fecha_fin": fecha_fin.strftime("%d/%m/%Y"),
        },
        "renglones": {
            "1_ingresos_brutos": ingresos_brutos,
            "2_ingresos_fuera_municipio": ingresos_fuera_municipio,
            "3_devoluciones_descuentos": devoluciones_descuentos,
            "4_base_gravable_neta": base_gravable_neta,
            "5_impuesto_neto_ica": impuesto_neto_ica,
            "6_impuesto_avisos_tableros_15pct": impuesto_avisos_tableros,
            "7_sobretasa_bomberil": sobretasa_bomberil,
            "8_total_impuesto_a_cargo": total_impuesto_a_cargo,
            "9_menos_retenciones_ica_a_favor": retenciones_favor,
            "10_total_saldo_a_pagar": saldo_neto_a_pagar,
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
