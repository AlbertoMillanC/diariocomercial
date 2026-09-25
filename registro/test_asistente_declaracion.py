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


class AsistenteDeclaracionTestCase(TestCase):
    def setUp(self):
        self.municipio = Municipio.objects.create(
            codigo_dane="15001",
            nombre="Tunja",
            departamento="Boyacá",
        )
        self.est = Establecimiento.objects.create(
            nombre="Tienda Provisional",
            nit="",
            municipio=self.municipio,
            configuracion_inicial_completada=False,
        )
        self.user = User.objects.create_user(username="propietario_test", password="password123")
        self.perfil = Perfil.objects.create(
            user=self.user,
            establecimiento=self.est,
            rol="propietario",
        )
        self.client = Client()
        self.client.login(username="propietario_test", password="password123")

    def test_inicio_muestra_banner_si_no_ha_completado_asistente(self):
        """Verifica que el panel de inicio invite al usuario a completar el asistente si es primera vez."""
        resp = self.client.get(reverse("inicio"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "¡Configure su Declaración Tributaria Renglón por Renglón!")
        self.assertContains(resp, reverse("asistente_inicial"))

    def test_asistente_inicial_get_carga_formulario_y_placeholders(self):
        """Verifica que la vista GET cargue con los placeholders y explicaciones de renglones."""
        resp = self.client.get(reverse("asistente_inicial"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Configuración Inicial: Declaración Renglón por Renglón")
        self.assertContains(resp, "Renglones 1 al 7")
        self.assertContains(resp, "Renglones 16 y 17")
        self.assertContains(resp, "Renglón 29")
        self.assertContains(resp, "Renglón 32")
        self.assertContains(resp, "Carnicería / Pescadería (4722 - 5‰)")

    def test_asistente_inicial_post_guarda_renglones_y_actividad(self):
        """Verifica el guardado exitoso de todos los renglones y la activación de la configuración."""
        data = {
            "nit": "900555666",
            "nombre": "Minimarket El Ahorro de Tunja",
            "direccion": "Carrera 11 # 19-45 Barrio Maldonado",
            "municipio": self.municipio.id,
            "telefono": "3105551234",
            "correo_reportes": "contador@elahorro.co",
            "clasificacion_tributaria": "comun",
            "ciiu_codigo": "4711",
            "ciiu_descripcion": "Comercio al por menor en establecimientos no especializados",
            "ciiu_tarifa_x_mil": "5.0",
            "motivo_nombre": "Venta Víveres y Abarrotes",
            "anticipo_ano_anterior": "120000",  # Renglón 29
            "saldo_favor_anterior": "30000",    # Renglón 32
            "resolucion_dian": "18764000001",
            "prefijo_facturacion": "FE",
            "consecutivo_inicial": "1",
        }

        resp = self.client.post(reverse("asistente_inicial"), data)
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse("declaracion_ica"))

        # Refrescar establecimiento
        self.est.refresh_from_db()
        self.assertEqual(self.est.nit, "900555666")
        self.assertEqual(self.est.nombre, "Minimarket El Ahorro de Tunja")
        self.assertEqual(self.est.direccion, "Carrera 11 # 19-45 Barrio Maldonado")
        self.assertEqual(self.est.correo_reportes, "contador@elahorro.co")
        self.assertEqual(self.est.llave_bre_b, "3105551234")
        self.assertEqual(self.est.anticipo_ano_anterior, Decimal("120000"))
        self.assertEqual(self.est.saldo_favor_anterior, Decimal("30000"))
        self.assertTrue(self.est.configuracion_inicial_completada)

        # Verificar Actividad CIIU creada
        act = ActividadCIIU.objects.get(establecimiento=self.est, codigo="4711")
        self.assertEqual(act.tarifa_x_mil, Decimal("5.0"))

        # Verificar Motivo predeterminado
        mot = MotivoVenta.objects.get(establecimiento=self.est, actividad=act, es_predeterminado=True)
        self.assertEqual(mot.nombre, "Venta Víveres y Abarrotes")

    def test_anticipos_y_saldos_descuentan_en_declaracion_sugerida(self):
        """Verifica que el Renglón 29 y Renglón 32 resten directamente del total a cargo."""
        # 1. Configurar establecimiento con anticipos y saldos
        self.est.anticipo_ano_anterior = Decimal("80000")  # Renglón 29
        self.est.saldo_favor_anterior = Decimal("20000")   # Renglón 32
        self.est.save()

        act = ActividadCIIU.objects.create(
            establecimiento=self.est,
            codigo="4722",
            descripcion="Carnicería",
            tarifa_x_mil=Decimal("5.0"),
        )
        mot = MotivoVenta.objects.create(
            establecimiento=self.est,
            actividad=act,
            nombre="Venta Carne",
            es_predeterminado=True,
        )

        # 2. Registrar venta de 50.000.000 COP -> Impuesto ICA 5 x mil = 250.000 COP
        # Avisos (15%): 37.500 | Bomberil (5%): 12.500 | Total impuesto cargo: 300.000 COP
        Venta.objects.create(
            establecimiento=self.est,
            actividad=act,
            motivo=mot,
            fecha_hora=timezone.now(),
            valor=Decimal("50000000"),
            usuario=self.user,
        )

        # 3. Liquidar declaración sugerida
        hoy = timezone.localdate()
        inicio_ano = hoy.replace(month=1, day=1)
        resultado = liquidar_declaracion_sugerida_ica(self.est, inicio_ano, hoy)
        renglones = resultado["renglones"]

        # Verificar renglones oficiales
        self.assertEqual(renglones["20_impuesto_industria_comercio"], Decimal("250000"))
        self.assertEqual(renglones["21_impuesto_avisos_tableros"], Decimal("37500"))
        self.assertEqual(renglones["23_sobretasa_bomberil"], Decimal("12500"))
        self.assertEqual(renglones["25_total_impuesto_a_cargo"], Decimal("300000"))

        # Renglón 29 y 32 descontados
        self.assertEqual(renglones["29_menos_anticipo_anterior"], Decimal("80000"))
        self.assertEqual(renglones["32_menos_saldo_favor_anterior"], Decimal("20000"))

        # Total saldo a pagar (Renglón 33 y 38): 300.000 - 80.000 - 20.000 = 200.000 COP
        self.assertEqual(renglones["33_total_saldo_a_cargo"], Decimal("200000"))
        self.assertEqual(renglones["38_total_a_pagar"], Decimal("200000"))
