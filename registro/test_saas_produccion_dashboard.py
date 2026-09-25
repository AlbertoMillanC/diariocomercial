from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from registro.models import (
    Establecimiento,
    Municipio,
    Perfil,
    Venta,
    Compra,
    PagoSuscripcion,
    ConfiguracionPlataformaSaaS,
    ConfiguracionSaaSMunicipio,
    obtener_configuracion_saas,
    VinculoCanal,
)
from registro.bot_service import despachar_mensaje


class SaasProduccionDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.superadmin = User.objects.create_superuser(
            username="superoperador", password="password123", email="admin@diariocomercial.co"
        )
        self.comerciante = User.objects.create_user(
            username="don_carlos", password="password123", email="carlos@carniceria.com"
        )

        self.mun_tunja = Municipio.objects.create(
            codigo_dane="15001", nombre="Tunja", departamento="Boyacá", activo=True
        )
        self.mun_bogota = Municipio.objects.create(
            codigo_dane="11001", nombre="Bogotá, D.C.", departamento="Bogotá, D.C.", activo=True
        )

        self.est_tunja = Establecimiento.objects.create(
            nombre="Carnicería El Buen Corte",
            nit="900123456-1",
            municipio=self.mun_tunja,
            estado="activo",
            plan_suscripcion="activo",
            llave_bre_b="3001112233",
            propietario_creador=self.comerciante,
        )
        self.perfil_comerciante = Perfil.objects.create(
            user=self.comerciante,
            establecimiento=self.est_tunja,
            rol="propietario",
        )

    def test_01_configuracion_saas_global_y_segmentacion_territorial(self):
        """Verifica que la configuración global SaaS y la segmentación por ciudad funcionen correctamente."""
        # 1. Configuración global por defecto
        cfg = obtener_configuracion_saas()
        self.assertEqual(cfg["llave_bre_b"], "3028530041")
        self.assertEqual(cfg["whatsapp_soporte"], "3146922087")
        self.assertEqual(cfg["tarifa_mensual_cop"], Decimal("19900"))
        self.assertFalse(cfg["segmentado"])

        # 2. Segmentación para Bogotá con número y llave propia
        ConfiguracionSaaSMunicipio.objects.create(
            municipio=self.mun_bogota,
            llave_bre_b="3109998877",
            whatsapp_soporte="3115554433",
            tarifa_mensual_cop=Decimal("24900"),
            activo=True,
        )

        cfg_bogota = obtener_configuracion_saas(self.mun_bogota)
        self.assertEqual(cfg_bogota["llave_bre_b"], "3109998877")
        self.assertEqual(cfg_bogota["whatsapp_soporte"], "3115554433")
        self.assertEqual(cfg_bogota["tarifa_mensual_cop"], Decimal("24900"))
        self.assertTrue(cfg_bogota["segmentado"])

        # 3. Tunja no tiene segmentación activa, usa la general
        cfg_tunja = obtener_configuracion_saas(self.mun_tunja)
        self.assertEqual(cfg_tunja["llave_bre_b"], "3028530041")
        self.assertEqual(cfg_tunja["whatsapp_soporte"], "3146922087")
        self.assertFalse(cfg_tunja["segmentado"])

    def test_02_superadmin_dashboard_graficos_y_consolidados(self):
        """Verifica que el dashboard de superadmin calcule las sumas consolidadas y las series de timeline."""
        # Crear ventas y compras
        hoy = timezone.localdate()
        Venta.objects.create(
            establecimiento=self.est_tunja,
            usuario=self.comerciante,
            fecha=hoy,
            valor=Decimal("50000"),
            medio_pago="bre_b",
            ica_estimado=Decimal("250"),
        )
        Compra.objects.create(
            establecimiento=self.est_tunja,
            usuario=self.comerciante,
            fecha=hoy,
            valor=Decimal("20000"),
            proveedor="Frigorífico Central",
        )
        PagoSuscripcion.objects.create(
            establecimiento=self.est_tunja,
            monto=Decimal("19900"),
            fecha_pago=hoy,
            periodo_dias=30,
            metodo="bre_b",
        )

        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse("superadmin_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Consolidado de Transacciones Globales")
        self.assertContains(resp, "chart-timeline")
        self.assertContains(resp, "chart-canales")
        self.assertContains(resp, "Carnicería El Buen Corte")
        self.assertEqual(resp.context["total_ventas_global"], Decimal("50000"))
        self.assertEqual(resp.context["total_compras_global"], Decimal("20000"))
        self.assertEqual(resp.context["flujo_neto_global"], Decimal("30000"))

    def test_03_api_metricas_tiempo_real(self):
        """Verifica que el endpoint JSON en tiempo real responda correctamente para Chart.js y feeds."""
        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse("superadmin_metricas_tiempo_real"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("totales", data)
        self.assertIn("timeline", data)
        self.assertIn("canales", data)
        self.assertIn("eventos", data)

    def test_04_bloqueo_cuenta_suspendida_y_proteccion_de_datos(self):
        """Verifica que suspender una cuenta proteja sus datos, redirija a cuenta_suspendida y avise en Telegram."""
        # 1. Registrar venta inicial
        v = Venta.objects.create(
            establecimiento=self.est_tunja,
            usuario=self.comerciante,
            fecha=timezone.localdate(),
            valor=Decimal("100000"),
            medio_pago="efectivo",
        )

        # 2. Suspender la tienda
        self.est_tunja.estado = "suspendido"
        self.est_tunja.save()

        # 3. La venta inicial y datos SIGUEN INTACTOS
        self.assertEqual(Venta.objects.filter(establecimiento=self.est_tunja).count(), 1)
        self.assertEqual(self.est_tunja.venta_set.first().valor, Decimal("100000"))

        # 4. Al intentar entrar a la web, el comerciante es redirigido a /cuenta-suspendida/
        self.client.force_login(self.comerciante)
        resp_web = self.client.get(reverse("venta_nueva"))
        self.assertRedirects(resp_web, reverse("cuenta_suspendida"))

        resp_suspendida = self.client.get(reverse("cuenta_suspendida"))
        self.assertEqual(resp_suspendida.status_code, 200)
        self.assertContains(resp_suspendida, "Tus Datos Están 100% Seguros y Respaldados")
        self.assertContains(resp_suspendida, "3028530041")
        self.assertContains(resp_suspendida, "3146922087")

        # 5. El bot de Telegram rechaza ventas operativas y ofrece reactivación
        VinculoCanal.objects.create(
            usuario=self.comerciante,
            establecimiento=self.est_tunja,
            canal="telegram",
            identificador_externo="999888",
            activo=True,
        )
        resp_bot = despachar_mensaje(
            canal="telegram",
            identificador_externo="999888",
            texto_mensaje="vender 20000 carne",
        )
        self.assertIn("Servicio Temporalmente Inactivo", resp_bot)
        self.assertIn("Tus ventas anteriores e inventario están 100% seguros", resp_bot)
        self.assertIn("3028530041", resp_bot)

    def test_05_guardar_configuracion_saas(self):
        """Verifica que el superadmin pueda modificar la llave Bre-B y WhatsApp central desde el formulario."""
        self.client.force_login(self.superadmin)
        resp = self.client.post(
            reverse("superadmin_configuracion"),
            {
                "accion": "guardar_global",
                "llave_bre_b_general": "3028530041",
                "tipo_llave_bre_b": "celular",
                "banco_receptor": "Bancolombia / Nequi",
                "whatsapp_soporte_general": "3146922087",
                "tarifa_mensual_cop": "19900",
                "correo_soporte": "soporte@diariocomercial.co",
                "dias_gracia_mora": "7",
                "smtp_host": "smtp-relay.brevo.com",
                "smtp_port": 587,
                "smtp_user": "test_user",
                "smtp_password": "test_password",
                "smtp_from_email": "soporte@diariocomercial.co",
            }
        )
        self.assertRedirects(resp, reverse("superadmin_configuracion"))
        cfg = ConfiguracionPlataformaSaaS.get_solo()
        self.assertEqual(cfg.dias_gracia_mora, 7)
