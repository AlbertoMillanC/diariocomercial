"""
registro/test_frontend_usabilidad.py
Pruebas de usabilidad, seguridad y verificación de integración frontend:
- Landing Page comercial con calculadora de ahorro.
- Asistente Móvil en configuración con PIN de 6 dígitos y Kill-Switch.
- Cobro rápido Bre-B dinámico (EMVCo) y simulación Webhook.
- Impresión térmica ESC/POS (58mm/80mm).
- Declaración Sugerida de ICA Tunja (Acuerdo 0032) y exportación para contador.
- Panel de Super-Administrador con control de acceso estricto.
"""
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from registro.models import (
    Establecimiento,
    Municipio,
    Perfil,
    ActividadCIIU,
    Venta,
    TransaccionBreB,
    TokenVinculacion,
    VinculoCanal,
)


class FrontendUsabilidadTests(TestCase):
    def setUp(self):
        self.municipio = Municipio.objects.create(
            codigo_dane="15001",
            nombre="Tunja",
            departamento="Boyacá",
        )
        self.est = Establecimiento.objects.create(
            nombre="Carnicería Boyacá Centro",
            nit="900999888",
            municipio=self.municipio,
            plan_suscripcion="activo",
            correo_reportes="contador.boyaca@tributaria.co",
        )
        self.owner = User.objects.create_user(
            username="don_ramon", password="password_ramon", first_name="Ramón", last_name="Pérez"
        )
        self.perfil_owner = Perfil.objects.create(
            user=self.owner, establecimiento=self.est, rol="propietario"
        )
        self.cajero = User.objects.create_user(
            username="pepe_cajero", password="password_pepe", first_name="Pepe", last_name="Cajero"
        )
        self.perfil_cajero = Perfil.objects.create(
            user=self.cajero, establecimiento=self.est, rol="dependiente"
        )
        self.staff_user = User.objects.create_user(
            username="super_carlos", password="password_carlos", is_superuser=True
        )

        self.ciiu = ActividadCIIU.objects.create(
            establecimiento=self.est,
            codigo="4722",
            descripcion="Comercio de carnes",
            tarifa_x_mil=Decimal("6.00"),
        )

    def test_01_landing_page_anonima(self):
        """La Landing Page pública carga para usuarios anónimos con la propuesta Bre-B y la calculadora."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No le regale el 3% de sus ventas")
        self.assertContains(resp, "Bre-B Banco de la República")
        self.assertContains(resp, "Plan Pioneros Tunja")
        self.assertContains(resp, "Plan Tendero Activo")

    def test_02_configuracion_asistente_movil_y_pin(self):
        """En Configuración se puede generar un PIN de 6 dígitos y Deep Link para Telegram."""
        self.client.force_login(self.owner)
        resp_gen = self.client.post("/configuracion/", {"accion": "generar_token_movil"})
        self.assertEqual(resp_gen.status_code, 302)

        resp_get = self.client.get("/configuracion/")
        self.assertEqual(resp_get.status_code, 200)
        pin_6 = resp_get.context["pin_6"]
        self.assertIsNotNone(pin_6)
        self.assertEqual(len(pin_6), 6)
        self.assertContains(resp_get, pin_6)
        self.assertContains(resp_get, "t.me/DiarioComercial_bot?start=auth_")

    def test_03_ciclo_completo_cobro_bre_b_y_webhook(self):
        """Generar cobro Bre-B en frontend, consultar status y confirmar vía webhook simulado."""
        self.client.force_login(self.cajero)

        # 1. Generar cobro
        resp_gen = self.client.get("/api/bre-b/generar/?monto=45000")
        self.assertEqual(resp_gen.status_code, 200)
        data = resp_gen.json()
        ref = data["referencia"]
        token_visual = data["token_visual"]
        self.assertTrue(ref.startswith("DC-"))
        self.assertTrue(token_visual.startswith("#"))
        self.assertEqual(data["monto"], 45000.0)

        # 2. Consultar status (iniciada)
        resp_st1 = self.client.get(f"/api/bre-b/status/{ref}/")
        self.assertEqual(resp_st1.json()["estado"], "iniciada")
        self.assertFalse(resp_st1.json()["aprobada"])

        # 3. Simular Webhook de BanRep
        resp_wh = self.client.get(f"/api/bre-b/mock-webhook/{ref}/")
        self.assertTrue(resp_wh.json()["exito"])
        venta_id = resp_wh.json()["venta_id"]
        self.assertIsNotNone(venta_id)

        # 4. Status ahora es aprobada
        resp_st2 = self.client.get(f"/api/bre-b/status/{ref}/")
        self.assertEqual(resp_st2.json()["estado"], "aprobada")
        self.assertTrue(resp_st2.json()["aprobada"])
        self.assertEqual(resp_st2.json()["venta_id"], venta_id)

        # 5. Descargar Ticket térmico ESC/POS
        resp_print = self.client.get(f"/ventas/{venta_id}/ticket-escpos/58/")
        self.assertEqual(resp_print.status_code, 200)
        self.assertEqual(resp_print["Content-Type"], "application/octet-stream")
        self.assertTrue(len(resp_print.content) > 20)

    def test_04_declaracion_sugerida_ica_tunja_seguridad_y_exportacion(self):
        """La declaración sugerida aplica Acuerdo 0032, exige ser propietario y permite exportar."""
        # 1. Dependiente bloqueado
        self.client.force_login(self.cajero)
        resp_bloq = self.client.get("/tributario/ica/")
        self.assertEqual(resp_bloq.status_code, 302)

        # 2. Propietario permitido
        self.client.force_login(self.owner)
        # Crear una venta para que liquide impuesto
        Venta.objects.create(
            establecimiento=self.est,
            usuario=self.owner,
            actividad=self.ciiu,
            fecha=timezone.localdate(),
            valor=Decimal("1000000"),
            concepto="Venta lomo",
        )
        resp_prop = self.client.get("/tributario/ica/")
        self.assertEqual(resp_prop.status_code, 200)
        self.assertContains(resp_prop, "Planilla de Liquidación Acuerdo 0032")
        self.assertContains(resp_prop, "AVISO LEGAL Y BLINDAJE TRIBUTARIO")
        self.assertContains(resp_prop, "15.00%")  # Avisos y tableros
        self.assertContains(resp_prop, "5.00%")   # Sobretasa bomberil

        # 3. Exportar anexo para contador
        resp_exp = self.client.get("/tributario/ica/exportar/")
        self.assertEqual(resp_exp.status_code, 200)
        self.assertIn("ANEXO TRIBUTARIO ICA", resp_exp.content.decode("utf-8"))
        self.assertIn("DISCLAIMER LEGAL", resp_exp.content.decode("utf-8"))

    def test_05_superadmin_dashboard_seguridad_y_toggle(self):
        """El dashboard de SuperAdmin está estrictamente reservado para staff y permite suspender."""
        # 1. Usuario normal redirigido a login
        self.client.force_login(self.owner)
        resp_unauth = self.client.get("/superadmin/")
        self.assertEqual(resp_unauth.status_code, 302)

        # 2. Staff user ingresa
        self.client.force_login(self.staff_user)
        resp_admin = self.client.get("/superadmin/")
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, "Panel de Super-Administrador SaaS")
        self.assertContains(resp_admin, "Carnicería Boyacá Centro")
        self.assertContains(resp_admin, "contador.boyaca@tributaria.co")

        # 3. Toggle de estado (Suspender comercio)
        self.assertEqual(self.est.estado, "activo")
        resp_toggle = self.client.get(f"/superadmin/establecimiento/{self.est.pk}/toggle-estado/")
        self.assertEqual(resp_toggle.status_code, 302)
        self.est.refresh_from_db()
        self.assertEqual(self.est.estado, "suspendido")

    def test_06_recuperar_password_flujo_completo(self):
        """El usuario puede restablecer su contraseña desde /recuperar-password/."""
        # 1. GET pantalla
        resp = self.client.get("/recuperar-password/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Recuperar Contraseña")

        # 2. POST solicitar enlace
        resp_post = self.client.post("/recuperar-password/", {
            "identificador": "don_ramon",
        })
        self.assertEqual(resp_post.status_code, 200)
        self.assertContains(resp_post, "Instrucciones Despachadas")

        # 3. Establecer nueva contraseña con token criptográfico válido
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes

        uidb64 = urlsafe_base64_encode(force_bytes(self.owner.pk))
        token = default_token_generator.make_token(self.owner)
        url_confirm = f"/recuperar-password/confirmar/{uidb64}/{token}/"

        resp_conf_get = self.client.get(url_confirm)
        self.assertEqual(resp_conf_get.status_code, 200)
        self.assertContains(resp_conf_get, "Nueva Contraseña")

        resp_conf_post = self.client.post(url_confirm, {
            "nueva_password": "nueva_clave_2026_segura",
            "confirmar_password": "nueva_clave_2026_segura",
        })
        self.assertEqual(resp_conf_post.status_code, 302)
        self.assertRedirects(resp_conf_post, "/login/")

        # 4. Iniciar sesión con la nueva clave
        resp_login = self.client.post("/login/", {
            "username": "don_ramon",
            "password": "nueva_clave_2026_segura",
        })
        self.assertEqual(resp_login.status_code, 302)

    def test_07_omnicanal_recibo_pdf_y_qr_bre_b(self):
        """Genera tarjeta QR Bre-B y recibo oficial en PDF listo para Telegram y WhatsApp."""
        from registro.recibo_service import (
            generar_imagen_qr_bre_b,
            generar_pdf_recibo_venta,
            preparar_paquete_omnicanal_venta,
        )
        venta = Venta.objects.create(
            establecimiento=self.est,
            usuario=self.owner,
            actividad=self.ciiu,
            fecha=timezone.localdate(),
            valor=Decimal("35000"),
            concepto="2 Kg de Sobrebarriga",
            medio_pago="bre_b",
        )
        paquete = preparar_paquete_omnicanal_venta(venta)
        self.assertIsNotNone(paquete["qr_bytes"])
        self.assertTrue(len(paquete["qr_bytes"]) > 1000)
        self.assertIsNotNone(paquete["pdf_bytes"])
        self.assertTrue(len(paquete["pdf_bytes"]) > 500)
        self.assertIn("messaging_product", paquete["whatsapp_fields"])
        self.assertEqual(paquete["whatsapp_fields"]["messaging_product"], "whatsapp")
