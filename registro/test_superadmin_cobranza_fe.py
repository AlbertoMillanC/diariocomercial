from decimal import Decimal
from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from registro.models import Establecimiento, Municipio, Perfil, Venta, Cliente, PagoSuscripcion, ActividadCIIU


class SuperadminCobranzaFacturacionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.municipio = Municipio.objects.create(nombre="Tunja", codigo_dane="15001")
        
        # Superusuario
        self.superadmin = User.objects.create_superuser(
            username="super_operador", email="admin@saas.co", password="adminpassword123"
        )

        # Comercio de prueba
        self.est = Establecimiento.objects.create(
            nombre="Carnicería El Buen Corte",
            nit="901234567-1",
            municipio=self.municipio,
            direccion="Calle 20 # 10-15",
            llave_bre_b="3109876543",
            tipo_llave_bre_b="celular",
            banco_receptor_bre_b="Bancolombia",
            plan_suscripcion="lanzamiento_cero",
            fecha_fin_prueba=timezone.localdate() + timedelta(days=5),
            prefijo_facturacion="FE",
            consecutivo_actual=1,
        )

        # Usuario dueño
        self.dueno = User.objects.create_user(
            username="don_carlos", email="carlos@buencorte.com", password="password123"
        )
        self.perfil_dueno = Perfil.objects.create(
            user=self.dueno, establecimiento=self.est, rol="propietario"
        )
        self.est.propietario_creador = self.dueno
        self.est.save()

        # Actividad CIIU
        self.act = ActividadCIIU.objects.create(
            establecimiento=self.est,
            codigo="4721",
            descripcion="Comercio al por menor de carnes",
            tarifa_x_mil=Decimal("5.00"),
        )

        # Cliente para Facturación Electrónica
        self.cliente = Cliente.objects.create(
            establecimiento=self.est,
            nombre="Restaurante La Casona SAS",
            nit_cedula="900987654-3",
            correo_electronico="compras@lacasona.com",
            telefono="3159998877",
            direccion="Carrera 9 # 19-30",
        )

        # Venta existente (consumidor final)
        self.venta = Venta.objects.create(
            establecimiento=self.est,
            usuario=self.dueno,
            actividad=self.act,
            fecha=timezone.localdate(),
            valor=Decimal("45000"),
            concepto="Venta carne de res",
            medio_pago="bre_b",
        )

    def test_superadmin_crear_comercio(self):
        """Verifica que el SuperAdmin pueda crear un nuevo comercio y su dueño."""
        self.client.force_login(self.superadmin)
        url = reverse("superadmin_comercio_crear")
        resp = self.client.post(url, {
            "nombre": "Droguería San Jerónimo",
            "nit": "900555666",
            "municipio": self.municipio.pk,
            "direccion": "Av. Colón # 12-40",
            "correo_reportes": "drogueria@correo.com",
            "llave_bre_b": "3201112233",
            "tipo_llave_bre_b": "celular",
            "banco_receptor_bre_b": "Daviplata",
            "plan_suscripcion": "lanzamiento_cero",
            "dias_vigencia": 30,
            "prefijo_facturacion": "FE",
            "consecutivo_inicial": 1,
            "crear_nuevo_usuario": True,
            "username_propietario": "drogueria_sanjeronimo",
            "password_propietario": "clave_segura_123",
        })
        self.assertEqual(resp.status_code, 302)
        nuevo_est = Establecimiento.objects.filter(nombre="Droguería San Jerónimo").first()
        self.assertIsNotNone(nuevo_est)
        self.assertEqual(nuevo_est.llave_bre_b, "3201112233")
        self.assertTrue(User.objects.filter(username="drogueria_sanjeronimo").exists())

    def test_superadmin_cobrar_y_asentar_pago_suscripcion(self):
        """Verifica el flujo de cobranza SaaS: cobro registrado extiende vigencia."""
        self.client.force_login(self.superadmin)
        url = reverse("superadmin_comercio_cobrar", kwargs={"pk": self.est.pk})
        
        # GET: Renderiza pantalla de cobranza, QR visual y mensaje de WhatsApp
        resp_get = self.client.get(url)
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, "Cobro y Gestión de Suscripción SaaS")
        self.assertContains(resp_get, "19.900")
        self.assertContains(resp_get, "3028530041") # Llave oficial Bre-B
        self.assertContains(resp_get, "3146922087") # WhatsApp técnico
        self.assertContains(resp_get, "data:image/png;base64,") # QR visual presente

        # Descarga de imagen QR pura (.PNG)
        url_qr = reverse("superadmin_descargar_qr_suscripcion", kwargs={"pk": self.est.pk})
        resp_qr = self.client.get(url_qr)
        self.assertEqual(resp_qr.status_code, 200)
        self.assertEqual(resp_qr["Content-Type"], "image/png")
        self.assertTrue(len(resp_qr.content) > 500)

        # POST: Asienta el pago de la mensualidad
        fecha_fin_previa = self.est.fecha_fin_prueba
        resp_post = self.client.post(url, {
            "monto": "19900",
            "metodo": "bre_b",
            "periodo_dias": 30,
            "referencia": "BREB-998877",
            "notas": "Mensualidad pagada oportunamente",
        })
        self.assertEqual(resp_post.status_code, 302)

        self.est.refresh_from_db()
        self.assertEqual(self.est.plan_suscripcion, "activo")
        self.assertTrue(self.est.fecha_fin_prueba > fecha_fin_previa)
        
        pago = PagoSuscripcion.objects.filter(establecimiento=self.est).first()
        self.assertIsNotNone(pago)
        self.assertEqual(pago.monto, Decimal("19900"))
        self.assertEqual(pago.referencia, "BREB-998877")

    def test_superadmin_eliminar_comercio_con_confirmacion(self):
        """Verifica la eliminación segura de un comercio tras confirmación explícita."""
        self.client.force_login(self.superadmin)
        url = reverse("superadmin_comercio_eliminar", kwargs={"pk": self.est.pk})
        
        # Confirmación incorrecta rechaza borrado
        resp_bad = self.client.post(url, {"confirmacion": "incorrecto"})
        self.assertEqual(resp_bad.status_code, 302)
        self.assertTrue(Establecimiento.objects.filter(pk=self.est.pk).exists())

        # Confirmación con nombre exacto elimina el comercio
        resp_ok = self.client.post(url, {"confirmacion": self.est.nombre, "motivo": "Cierre"})
        self.assertEqual(resp_ok.status_code, 302)
        self.assertFalse(Establecimiento.objects.filter(pk=self.est.pk).exists())

    def test_facturacion_electronica_dashboard_y_emision(self):
        """Verifica el centro de facturación electrónica y la conversión de venta a Factura DIAN."""
        self.client.force_login(self.dueno)
        
        # Dashboard de Facturación Electrónica
        resp_dash = self.client.get(reverse("facturacion_electronica_dashboard"))
        self.assertEqual(resp_dash.status_code, 200)
        self.assertContains(resp_dash, "Centro de Facturación Electrónica DIAN")

        # Convertir venta a Factura Electrónica
        url_emitir = reverse("venta_emitir_factura_electronica", kwargs={"pk": self.venta.pk})
        resp_emitir = self.client.post(url_emitir, {"cliente_id": self.cliente.pk})
        self.assertEqual(resp_emitir.status_code, 302)

        self.venta.refresh_from_db()
        self.assertTrue(self.venta.solicita_factura_electronica)
        self.assertEqual(self.venta.numero_factura_electronica, "FE-00001")
        self.assertTrue(len(self.venta.cufe) > 30)
        self.assertEqual(self.venta.estado_dian, "aprobada")

        # Ver detalle imprimible de Factura Electrónica
        resp_det = self.client.get(reverse("factura_electronica_detalle", kwargs={"pk": self.venta.pk}))
        self.assertEqual(resp_det.status_code, 200)
        self.assertContains(resp_det, "FE-00001")
        self.assertContains(resp_det, "Restaurante La Casona SAS")
        self.assertContains(resp_det, "CUFE")

        # Descargar PDF de Factura Electrónica
        resp_pdf = self.client.get(reverse("factura_electronica_pdf", kwargs={"pk": self.venta.pk}))
        self.assertEqual(resp_pdf.status_code, 200)
        self.assertEqual(resp_pdf["Content-Type"], "application/pdf")
        self.assertTrue(len(resp_pdf.content) > 1000)
