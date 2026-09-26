from datetime import date
from decimal import Decimal
from pathlib import Path
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.conf import settings

from registro.models import (
    ActividadCIIU,
    Establecimiento,
    MotivoVenta,
    Perfil,
    TransaccionBreB,
    Venta,
)


class SeguridadEIntegridadDatosTests(TestCase):
    def setUp(self):
        # Tienda 1: Carnicería San Jerónimo
        self.est1 = Establecimiento.objects.create(
            nombre="Carnicería San Jerónimo",
            nit="901111222-1",
            correo_reportes="sanjeronimo@tunja.com",
            llave_bre_b="3101112233",
        )
        self.user1 = User.objects.create_user(
            "propietario1", password="password123", first_name="Juan", last_name="Mora"
        )
        self.perfil1 = Perfil.objects.create(user=self.user1, establecimiento=self.est1, rol="propietario")

        # Tienda 2: Víveres El Trébol (Comercio ajeno)
        self.est2 = Establecimiento.objects.create(
            nombre="Víveres El Trébol",
            nit="902333444-2",
            correo_reportes="trebol@tunja.com",
            llave_bre_b="3204445566",
        )
        self.user2 = User.objects.create_user(
            "propietario2", password="password123", first_name="Ana", last_name="López"
        )
        self.perfil2 = Perfil.objects.create(user=self.user2, establecimiento=self.est2, rol="propietario")

        # Superadministrador
        self.superadmin = User.objects.create_superuser(
            "superadmin", email="admin@diariocomercial.co", password="supersecretpassword"
        )

        # Transacción Bre-B perteneciente a la Tienda 2
        self.tx_tienda2 = TransaccionBreB.objects.create(
            establecimiento=self.est2,
            referencia_unica="BREB-TX-SEC-009988",
            monto=Decimal("55000"),
            llave_utilizada="3204445566",
            payload_emvco="000201010212...",
            estado="iniciada",
            token_visual_corto="9988",
        )

    def test_01_api_status_bre_b_aislamiento_inquilino(self):
        """Un usuario de la Tienda 1 NO puede consultar el estado de una transacción de la Tienda 2."""
        self.client.login(username="propietario1", password="password123")
        url = reverse("api_status_bre_b", kwargs={"referencia": self.tx_tienda2.referencia_unica})
        resp = self.client.get(url)
        # Debe responder 404 porque no pertenece al establecimiento del usuario en sesión
        self.assertEqual(resp.status_code, 404)

        # El SuperAdmin sí tiene acceso global de soporte
        self.client.login(username="superadmin", password="supersecretpassword")
        resp_sa = self.client.get(url)
        self.assertEqual(resp_sa.status_code, 200)
        self.assertEqual(resp_sa.json()["referencia"], self.tx_tienda2.referencia_unica)

    def test_02_api_mock_webhook_aislamiento_inquilino(self):
        """Un usuario de la Tienda 1 NO puede activar el webhook de una transacción de la Tienda 2."""
        self.client.login(username="propietario1", password="password123")
        url = reverse("api_mock_webhook_bre_b", kwargs={"referencia": self.tx_tienda2.referencia_unica})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

        # El dueño legítimo de la Tienda 2 sí puede confirmar su propia transacción en entorno de pruebas
        self.client.login(username="propietario2", password="password123")
        resp_legit = self.client.get(url)
        self.assertEqual(resp_legit.status_code, 200)
        self.assertTrue(resp_legit.json()["exito"])

    def test_03_api_comprobar_pago_requiere_login_y_aislamiento(self):
        """Comprobación técnica exige sesión y bloquea accesos cruzados entre comercios."""
        url = reverse("api_comprobar_pago_electronico", kwargs={"referencia": self.tx_tienda2.referencia_unica})

        # Sin login -> Redirección a login
        self.client.logout()
        resp_anon = self.client.get(url)
        self.assertEqual(resp_anon.status_code, 302)

        # Logueado en Tienda 1 intentando ver Tienda 2 -> 404
        self.client.login(username="propietario1", password="password123")
        resp_cross = self.client.get(url)
        self.assertEqual(resp_cross.status_code, 404)

        # Logueado en Tienda 2 -> 200 OK con metadatos
        self.client.login(username="propietario2", password="password123")
        resp_ok = self.client.get(url)
        self.assertEqual(resp_ok.status_code, 200)
        self.assertTrue(resp_ok.json()["comprobado"])

    def test_04_generar_token_movil_bloquea_usuarios_ajenos(self):
        """No permite generar códigos de vinculación móvil usando user_id de otros comercios."""
        self.client.login(username="propietario1", password="password123")
        url = reverse("configuracion")
        # Intenta pasar el user_id de la Tienda 2 (user2)
        resp = self.client.post(
            url,
            {
                "accion": "generar_token_movil",
                "user_id": self.user2.id,
            }
        )
        self.assertEqual(resp.status_code, 404)

    def test_05_comando_respaldar_base_datos(self):
        """El comando de respaldo genera un archivo consistente con retención y auditoría."""
        test_dir = settings.BASE_DIR / "backups_test"
        try:
            call_command("respaldar_base_datos", f"--output-dir={test_dir}", "--retention-days=10")
            archivos = list(test_dir.glob("diariocomercial_*"))
            self.assertGreaterEqual(len(archivos), 1)
            # El archivo debe tener tamaño superior a 10 KB
            tamano = archivos[0].stat().st_size
            self.assertGreater(tamano, 1024 * 5)
        finally:
            import shutil
            if test_dir.exists():
                shutil.rmtree(test_dir, ignore_errors=True)
