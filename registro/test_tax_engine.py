"""
registro/test_tax_engine.py
Pruebas para el Motor Tributario Paramétrico Multi-Municipio (ICA, Avisos, Bomberos)
y el módulo de Exógena Municipal.
"""
from decimal import Decimal
from datetime import date
from django.test import TestCase
from django.contrib.auth.models import User

from registro.models import (
    Establecimiento,
    Municipio,
    Perfil,
    ActividadCIIU,
    Venta,
    Retencion,
    ReglaTributariaMunicipio,
    RegistroExogenaMunicipal,
)
from registro.tax_engine import (
    liquidar_declaracion_sugerida_ica,
    registrar_evento_exogena,
    generar_resumen_exogena_anual,
)


class TaxEngineTests(TestCase):
    def setUp(self):
        # 1. Municipio Tunja
        self.municipio_tunja = Municipio.objects.create(
            codigo_dane="15001", nombre="Tunja", departamento="Boyacá"
        )
        self.regla_tunja = ReglaTributariaMunicipio.objects.create(
            municipio=self.municipio_tunja,
            porcentaje_avisos_y_tableros=Decimal("15.00"),
            porcentaje_sobretasa_bomberil=Decimal("5.00"),
            acuerdo_municipal_referencia="Acuerdo 0032 de 2020 Tunja",
        )

        # Establecimiento en Tunja
        self.tienda = Establecimiento.objects.create(
            nombre="Minimarket El Sol",
            nit="901234567-8",
            municipio=self.municipio_tunja,
        )
        self.user = User.objects.create_user(username="don_jose", password="password123")
        self.perfil = Perfil.objects.create(
            user=self.user, establecimiento=self.tienda, rol="propietario"
        )
        self.act_viveres = ActividadCIIU.objects.create(
            establecimiento=self.tienda, codigo="4711", descripcion="Víveres y abarrotes", tarifa_x_mil=Decimal("6.0")
        )

        # 2. Registrar ventas en el bimestre (Enero - Febrero 2026)
        # Venta 1: $10.000.000 COP
        self.v1 = Venta.objects.create(
            establecimiento=self.tienda,
            usuario=self.user,
            actividad=self.act_viveres,
            fecha=date(2026, 1, 15),
            valor=Decimal("10000000.00"),
            concepto="Venta enero",
            estado="vigente",
        )
        # Venta 2: $5.000.000 COP
        self.v2 = Venta.objects.create(
            establecimiento=self.tienda,
            usuario=self.user,
            actividad=self.act_viveres,
            fecha=date(2026, 2, 20),
            valor=Decimal("5000000.00"),
            concepto="Venta febrero",
            estado="vigente",
        )

        # ReteICA que un cliente empresa le practicó a la tienda: $30.000 COP
        self.ret = Retencion.objects.create(
            establecimiento=self.tienda,
            usuario=self.user,
            fecha=date(2026, 2, 25),
            tipo="ica",
            valor=Decimal("30000.00"),
            tercero="Empresa Cliente SAS",
            estado="vigente",
        )

    def test_declaracion_sugerida_ica_tunja(self):
        """
        Valida la liquidación matemática oficial para Tunja:
        - Ingresos brutos: $15.000.000 COP
        - Impuesto neto ICA (6 x 1000): $90.000 COP
        - Avisos y Tableros (15% de $90.000): $13.500 COP
        - Sobretasa Bomberil (5% de $90.000): $4.500 COP
        - Total impuesto a cargo: $108.000 COP
        - Menos ReteICA a favor: $30.000 COP
        - Saldo neto a pagar: $78.000 COP
        """
        liquidacion = liquidar_declaracion_sugerida_ica(
            establecimiento=self.tienda,
            fecha_inicio=date(2026, 1, 1),
            fecha_fin=date(2026, 2, 28),
        )

        renglones = liquidacion["renglones"]
        self.assertEqual(renglones["1_ingresos_brutos"], Decimal("15000000.00"))
        self.assertEqual(renglones["4_base_gravable_neta"], Decimal("15000000.00"))
        self.assertEqual(renglones["5_impuesto_neto_ica"], Decimal("90000.00"))
        self.assertEqual(renglones["6_impuesto_avisos_tableros_15pct"], Decimal("13500.00"))
        self.assertEqual(renglones["7_sobretasa_bomberil"], Decimal("4500.00"))
        self.assertEqual(renglones["8_total_impuesto_a_cargo"], Decimal("108000.00"))
        self.assertEqual(renglones["9_menos_retenciones_ica_a_favor"], Decimal("30000.00"))
        self.assertEqual(renglones["10_total_saldo_a_pagar"], Decimal("78000.00"))
        self.assertIn("Tunja", liquidacion["municipio"])

    def test_parametracion_otro_municipio(self):
        """Si la tienda está en Sogamoso con reglas distintas, el motor adapta el cálculo."""
        mun_sogamoso = Municipio.objects.create(
            codigo_dane="15759", nombre="Sogamoso", departamento="Boyacá"
        )
        ReglaTributariaMunicipio.objects.create(
            municipio=mun_sogamoso,
            porcentaje_avisos_y_tableros=Decimal("15.00"),
            porcentaje_sobretasa_bomberil=Decimal("7.00"), # 7% en Sogamoso
            acuerdo_municipal_referencia="Acuerdo Sogamoso 2024",
        )
        self.tienda.municipio = mun_sogamoso
        self.tienda.save()

        liquidacion = liquidar_declaracion_sugerida_ica(
            establecimiento=self.tienda,
            fecha_inicio=date(2026, 1, 1),
            fecha_fin=date(2026, 2, 28),
        )

        renglones = liquidacion["renglones"]
        # Con 7% de bomberil: $90.000 * 0.07 = $6.300
        self.assertEqual(renglones["7_sobretasa_bomberil"], Decimal("6300.00"))
        self.assertEqual(renglones["8_total_impuesto_a_cargo"], Decimal("109800.00"))

    def test_recoleccion_y_resumen_exogena_municipal(self):
        """Prueba el registro de operaciones y consolidado para medios magnéticos."""
        # Registrar compra a proveedor mayorista
        reg_compra = registrar_evento_exogena(
            establecimiento=self.tienda,
            tipo_registro="compra",
            nit_tercero="800123456",
            nombre_razon_social="Distribuidora Mayorista de Víveres Boyacá SAS",
            monto_base=Decimal("4500000.00"),
            monto_impuesto_retencion=Decimal("27000.00"), # ReteICA practicado
            tarifa_aplicada=Decimal("6.0"),
            direccion_tercero="Carrera 10 # 15-20 Tunja",
            fecha_transaccion=date(2026, 1, 20),
        )
        self.assertEqual(reg_compra.año_gravable, 2026)

        # Generar consolidado anual
        resumen = generar_resumen_exogena_anual(self.tienda, 2026)
        self.assertEqual(resumen["año_gravable"], 2026)
        self.assertEqual(resumen["resumen"]["total_compras_reportadas"], Decimal("4500000.00"))
        self.assertEqual(resumen["conteo_terceros_unicos"], 1)
