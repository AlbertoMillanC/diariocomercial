from datetime import date, timedelta
from decimal import Decimal
import io
import openpyxl

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from registro.models import (
    ActividadCIIU,
    Compra,
    EnvioReporte,
    Establecimiento,
    MotivoVenta,
    Perfil,
    Producto,
    Venta,
)
from registro.reporte_excel_service import generar_excel_reporte_periodico


class ReportesAutomaticosExcelTests(TestCase):
    def setUp(self):
        self.est = Establecimiento.objects.create(
            nombre="Super Carnes Boyacá",
            nit="900999888-1",
            correo_reportes="gerencia@supercarnes.com",
            reportes_automaticos_activos=True,
            frecuencia_reporte_automatico="hora",
        )
        self.user_owner = User.objects.create_user(
            "don.pedro", password="password123", first_name="Pedro", last_name="Pérez"
        )
        Perfil.objects.create(user=self.user_owner, establecimiento=self.est, rol="propietario")

        self.ciiu = ActividadCIIU.objects.create(
            establecimiento=self.est,
            codigo="4722",
            descripcion="Carnicería",
            tarifa_x_mil=Decimal("7.00"),
        )
        self.motivo = MotivoVenta.objects.create(
            establecimiento=self.est,
            actividad=self.ciiu,
            nombre="Venta de Carnes",
            es_predeterminado=True,
        )

        self.producto = Producto.objects.create(
            establecimiento=self.est,
            codigo_barras="770111222333",
            nombre="Lomo Fino de Res",
            categoria="Carnes",
            stock_kilos=Decimal("25.5"),
            precio_kilo=Decimal("38000"),
            costo_unitario=Decimal("28000"),
            unidad_medida="kg",
        )

        self.hoy = date.today()
        # Create sample sales with different payment methods
        self.venta_efectivo = Venta.objects.create(
            establecimiento=self.est,
            usuario=self.user_owner,
            actividad=self.ciiu,
            motivo=self.motivo,
            fecha=self.hoy,
            valor=Decimal("76000"),
            medio_pago="efectivo",
            ica_estimado=Decimal("532"),
        )
        self.venta_bre_b = Venta.objects.create(
            establecimiento=self.est,
            usuario=self.user_owner,
            actividad=self.ciiu,
            motivo=self.motivo,
            fecha=self.hoy,
            valor=Decimal("114000"),
            medio_pago="bre_b",
            ica_estimado=Decimal("798"),
        )

        # Create sample purchase
        self.compra = Compra.objects.create(
            establecimiento=self.est,
            usuario=self.user_owner,
            fecha=self.hoy,
            proveedor="Frigorífico Central de Tunja",
            concepto="Media res bovina",
            valor=Decimal("150000"),
        )

    def test_generar_excel_reporte_periodico_valido(self):
        """Genera un archivo Excel real en memoria y valida las 4 hojas de trabajo."""
        excel_bytes, filename = generar_excel_reporte_periodico(
            self.est, self.hoy, self.hoy, "Reporte Turno 1"
        )
        self.assertTrue(filename.endswith(".xlsx"))
        self.assertGreater(len(excel_bytes), 1000)

        # Cargar con openpyxl para verificar la estructura interna
        wb = openpyxl.load_workbook(io.BytesIO(excel_bytes), data_only=True)
        sheet_names = wb.sheetnames
        self.assertIn("Resumen y Arqueo", sheet_names)
        self.assertIn("Detalle Ventas", sheet_names)
        self.assertIn("Compras y Gastos", sheet_names)
        self.assertIn("Inventario Actual", sheet_names)

        # Validar contenido en Detalle Ventas
        ws_ventas = wb["Detalle Ventas"]
        valores_columna_pago = [cell.value for cell in ws_ventas["D"]]
        self.assertTrue(any("efectivo" in str(v).lower() for v in valores_columna_pago))
        self.assertTrue(any("bre-b" in str(v).lower() for v in valores_columna_pago))

        # Validar contenido en Inventario Actual
        ws_inv = wb["Inventario Actual"]
        nombres_productos = [cell.value for cell in ws_inv["B"]]
        self.assertIn("Lomo Fino de Res", nombres_productos)

    def test_comando_despachar_reportes_automaticos(self):
        """Ejecuta el management command de despacho periódico y comprueba el envío del email."""
        mail.outbox.clear()
        call_command("despachar_reportes_automaticos", "--force")

        self.assertEqual(len(mail.outbox), 1)
        correo_enviado = mail.outbox[0]
        self.assertIn("gerencia@supercarnes.com", correo_enviado.to)
        self.assertIn("Super Carnes Boyacá", correo_enviado.subject)
        self.assertEqual(len(correo_enviado.attachments), 1)

        nombre_adjunto, contenido, mimetype = correo_enviado.attachments[0]
        self.assertTrue(nombre_adjunto.endswith(".xlsx"))
        self.assertIn("spreadsheetml", mimetype)

        # Verificar que se actualizó el establecimiento
        self.est.refresh_from_db()
        self.assertIsNotNone(self.est.ultimo_reporte_automatico)

        # Verificar que se registró la auditoría en EnvioReporte
        envio = EnvioReporte.objects.filter(establecimiento=self.est).first()
        self.assertIsNotNone(envio)
        self.assertEqual(envio.estado_envio, "automatico")
        self.assertEqual(envio.total_ingresos, Decimal("190000"))

    def test_vista_descargar_excel_directo(self):
        """La vista enviar_reporte con formato=excel descarga directamente el archivo .xlsx."""
        self.client.login(username="don.pedro", password="password123")
        url = reverse("enviar_reporte") + f"?desde={self.hoy}&hasta={self.hoy}&formato=excel"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn(".xlsx", resp["Content-Disposition"])
        self.assertGreater(len(resp.content), 1000)

    def test_vista_guardar_programacion(self):
        """Guarda la nueva frecuencia y correo desde la interfaz web."""
        self.client.login(username="don.pedro", password="password123")
        resp = self.client.post(
            reverse("enviar_reporte"),
            {
                "accion": "guardar_programacion",
                "correo_reportes": "nuevo_contador@tunjacontas.com",
                "reportes_automaticos_activos": "on",
                "frecuencia_reporte_automatico": "turno",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.est.refresh_from_db()
        self.assertEqual(self.est.correo_reportes, "nuevo_contador@tunjacontas.com")
        self.assertEqual(self.est.frecuencia_reporte_automatico, "turno")
        self.assertTrue(self.est.reportes_automaticos_activos)

    def test_vista_probar_envio_ahora(self):
        """Dispara un envío inmediato de prueba con Excel adjunto desde la interfaz."""
        self.client.login(username="don.pedro", password="password123")
        mail.outbox.clear()
        resp = self.client.post(
            reverse("enviar_reporte"),
            {
                "accion": "probar_envio_ahora",
                "desde": str(self.hoy),
                "hasta": str(self.hoy),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertIn("gerencia@supercarnes.com", correo.to)
        self.assertEqual(len(correo.attachments), 1)
        self.assertTrue(correo.attachments[0][0].endswith(".xlsx"))
