from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from registro.models import (
    Establecimiento,
    Municipio,
    Perfil,
    ActividadCIIU,
    MotivoVenta,
    Venta,
)
from registro.tax_engine import liquidar_declaracion_sugerida_ica


class MultiTiendaRentaDianTestCase(TestCase):
    def setUp(self):
        self.municipio_tunja = Municipio.objects.create(
            codigo_dane="15001",
            nombre="Tunja",
            departamento="Boyacá",
        )
        self.municipio_duitama = Municipio.objects.create(
            codigo_dane="15238",
            nombre="Duitama",
            departamento="Boyacá",
        )

        # Usuario Empresario Don Pedro (dueño de 2 tiendas)
        self.user_pedro = User.objects.create_user(username="don_pedro", password="password123")

        # Tienda 1 en Tunja
        self.tienda_tunja = Establecimiento.objects.create(
            nombre="Carnicería San Pedro - Tunja Centro",
            nit="900111222",
            municipio=self.municipio_tunja,
            propietario_creador=self.user_pedro,
            declara_renta_dian=True,
            otros_ingresos_nacionales_anual=Decimal("50000000"),  # Ingresos adicionales fuera del municipio
        )
        self.perfil_pedro = Perfil.objects.create(
            user=self.user_pedro,
            establecimiento=self.tienda_tunja,
            rol="propietario",
        )

        # Tienda 2 en Duitama (del mismo dueño don Pedro)
        self.tienda_duitama = Establecimiento.objects.create(
            nombre="Carnicería San Pedro - Sede Duitama",
            nit="900111222",
            municipio=self.municipio_duitama,
            propietario_creador=self.user_pedro,
            declara_renta_dian=True,
        )

        # Otro comerciante ajeno: Doña María
        self.user_maria = User.objects.create_user(username="dona_maria", password="password123")
        self.tienda_maria = Establecimiento.objects.create(
            nombre="Fruver Doña María",
            nit="1049333444",
            municipio=self.municipio_tunja,
            propietario_creador=self.user_maria,
        )
        self.perfil_maria = Perfil.objects.create(
            user=self.user_maria,
            establecimiento=self.tienda_maria,
            rol="propietario",
        )

        self.client_pedro = Client()
        self.client_pedro.login(username="don_pedro", password="password123")

    def test_rol_empresario_y_establecimientos_propios(self):
        """Verifica que Don Pedro tenga acceso exclusivamente a sus 2 tiendas."""
        self.assertTrue(self.perfil_pedro.es_empresario())
        propias = list(self.perfil_pedro.establecimientos_propios())
        self.assertIn(self.tienda_tunja, propias)
        self.assertIn(self.tienda_duitama, propias)
        self.assertNotIn(self.tienda_maria, propias)  # Aislamiento estricto

    def test_store_switcher_cambio_tienda_seguro(self):
        """Prueba que el empresario pueda alternar entre sus tiendas pero no a tiendas ajenas."""
        # 1. Cambiar a Sede Duitama (Propia)
        resp = self.client_pedro.get(reverse("tiendas_cambiar_activa", args=[self.tienda_duitama.pk]), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.perfil_pedro.refresh_from_db()
        self.assertEqual(self.perfil_pedro.establecimiento, self.tienda_duitama)

        # 2. Intentar cambiar a la tienda de Doña María (Ajena) -> Debe arrojar 404
        resp_ajena = self.client_pedro.get(reverse("tiendas_cambiar_activa", args=[self.tienda_maria.pk]))
        self.assertEqual(resp_ajena.status_code, 404)

    def test_panel_consolidado_multi_tienda(self):
        """Comprueba el dashboard consolidado multi-tienda con las métricas sumadas."""
        ahora = timezone.now()
        act1 = ActividadCIIU.objects.create(establecimiento=self.tienda_tunja, codigo="4722", tarifa_x_mil=Decimal("5.0"))
        mot1 = MotivoVenta.objects.create(establecimiento=self.tienda_tunja, actividad=act1, nombre="Carne Tunja")
        act2 = ActividadCIIU.objects.create(establecimiento=self.tienda_duitama, codigo="4722", tarifa_x_mil=Decimal("5.0"))
        mot2 = MotivoVenta.objects.create(establecimiento=self.tienda_duitama, actividad=act2, nombre="Carne Duitama")

        # Venta Tunja: 30.000.000 COP
        Venta.objects.create(
            establecimiento=self.tienda_tunja,
            actividad=act1,
            motivo=mot1,
            fecha_hora=ahora,
            valor=Decimal("30000000"),
            usuario=self.user_pedro,
        )
        # Venta Duitama: 20.000.000 COP
        Venta.objects.create(
            establecimiento=self.tienda_duitama,
            actividad=act2,
            motivo=mot2,
            fecha_hora=ahora,
            valor=Decimal("20000000"),
            usuario=self.user_pedro,
        )

        resp = self.client_pedro.get(reverse("tiendas_lista_consolidada"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Panel Consolidado de mis Establecimientos")
        self.assertContains(resp, "Carnicería San Pedro - Tunja Centro")
        self.assertContains(resp, "Carnicería San Pedro - Sede Duitama")
        self.assertNotContains(resp, "Fruver Doña María")  # Cero fuga de datos

    def test_cruce_renta_dian_renglon_8_y_9_ica(self):
        """
        REGLA CLAVE DE USUARIO:
        Si declara renta a la DIAN, en el Renglón 8 debe consolidar con otros ingresos
        a nivel nacional o de otros establecimientos, y en el Renglón 9 restar lo que no sea de Tunja.
        """
        ahora = timezone.now()
        inicio_ano = ahora.replace(month=1, day=1).date()
        hoy = ahora.date()

        act_tunja = ActividadCIIU.objects.create(establecimiento=self.tienda_tunja, codigo="4722", tarifa_x_mil=Decimal("5.0"))
        mot_tunja = MotivoVenta.objects.create(establecimiento=self.tienda_tunja, actividad=act_tunja, nombre="Carne")

        act_duitama = ActividadCIIU.objects.create(establecimiento=self.tienda_duitama, codigo="4722", tarifa_x_mil=Decimal("5.0"))
        mot_duitama = MotivoVenta.objects.create(establecimiento=self.tienda_duitama, actividad=act_duitama, nombre="Carne")

        # Ventas Tunja: 40.000.000 COP
        Venta.objects.create(
            establecimiento=self.tienda_tunja,
            actividad=act_tunja,
            motivo=mot_tunja,
            fecha_hora=ahora,
            valor=Decimal("40000000"),
            usuario=self.user_pedro,
        )
        # Ventas Sede Duitama: 60.000.000 COP
        Venta.objects.create(
            establecimiento=self.tienda_duitama,
            actividad=act_duitama,
            motivo=mot_duitama,
            fecha_hora=ahora,
            valor=Decimal("60000000"),
            usuario=self.user_pedro,
        )
        # Otros ingresos declarados fuera del municipio: 50.000.000 COP (definidos en setUp)

        # Liquidar ICA Tunja
        resultado = liquidar_declaracion_sugerida_ica(self.tienda_tunja, inicio_ano, hoy)
        renglones = resultado["renglones"]

        # 1. Total ingresos país Renglón 8 = Tunja (40M) + Duitama (60M) + Otros (50M) = 150.000.000 COP
        # Este valor es el que coincide 100% con la Declaración de Renta DIAN
        self.assertEqual(renglones["8_total_ingresos_pais"], Decimal("150000000"))

        # 2. Menos ingresos fuera del municipio Renglón 9 = Duitama (60M) + Otros (50M) = 110.000.000 COP
        self.assertEqual(renglones["9_ingresos_fuera_municipio"], Decimal("110000000"))

        # 3. Total ingresos gravables en Tunja Renglón 10 = R8 (150M) - R9 (110M) = 40.000.000 COP
        self.assertEqual(renglones["10_total_ingresos_municipio"], Decimal("40000000"))

        # 4. Impuesto ICA liquidado sobre 40.000.000 x 5 por mil = 200.000 COP
        self.assertEqual(renglones["20_impuesto_industria_comercio"], Decimal("200000"))
