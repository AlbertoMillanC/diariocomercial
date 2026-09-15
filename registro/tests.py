from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from registro.models import (
    ActividadCIIU,
    Auditoria,
    Compra,
    EnvioReporte,
    Establecimiento,
    MotivoVenta,
    Perfil,
    Retencion,
    Venta,
)


class DiarioComercialTests(TestCase):
    def setUp(self):
        self.est = Establecimiento.objects.create(
            nombre="Minimarket El Parque",
            nit="900123456",
            correo_reportes="contador.tunja@correo.com",
        )
        self.maria = User.objects.create_user(
            "maria.gomez", password="tunja2026", first_name="María", last_name="Gómez"
        )
        self.carlos = User.objects.create_user(
            "carlos.ruiz", password="tunja2026", first_name="Carlos", last_name="Ruiz"
        )
        Perfil.objects.create(user=self.maria, establecimiento=self.est, rol="propietario")
        Perfil.objects.create(user=self.carlos, establecimiento=self.est, rol="dependiente")
        self.ciiu = ActividadCIIU.objects.create(
            establecimiento=self.est,
            codigo="4711",
            descripcion="Víveres",
            tarifa_x_mil=Decimal("6.00"),
        )
        self.motivo = MotivoVenta.objects.create(
            establecimiento=self.est,
            actividad=self.ciiu,
            nombre="Venta de víveres",
            es_predeterminado=True,
        )
        self.hoy = date.today()

    def _login(self, user="maria.gomez", password="tunja2026"):
        ok = self.client.login(username=user, password=password)
        self.assertTrue(ok)

    def test_rf01_login_por_rol_y_usuario_inactivo(self):
        resp = self.client.post(
            reverse("login"), {"username": "maria.gomez", "password": "tunja2026"}
        )
        self.assertEqual(resp.status_code, 302)

        self.client.logout()
        self.client.post(reverse("login"), {"username": "maria.gomez", "password": "mala"})
        self.assertTrue(Auditoria.objects.filter(accion="login_fallido").exists())

        self.carlos.is_active = False
        self.carlos.save()
        resp = self.client.post(
            reverse("login"), {"username": "carlos.ruiz", "password": "tunja2026"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "inactivo")

    def test_rf02_registrar_venta_con_observacion(self):
        self._login("carlos.ruiz")
        resp = self.client.post(
            reverse("venta_nueva"),
            {
                "fecha_hora": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "actividad": self.ciiu.pk,
                "motivo": self.motivo.pk,
                "valor": "25000",
                "tipo_cliente": "particular",
                "observacion": "domicilio",
            },
        )
        self.assertEqual(resp.status_code, 302)
        venta = Venta.objects.get()
        self.assertEqual(venta.valor, Decimal("25000"))
        self.assertEqual(venta.observacion, "domicilio")
        self.assertEqual(venta.estado, "vigente")
        self.assertEqual(venta.ica_estimado, Decimal("150.00"))

        resp = self.client.post(
            reverse("venta_nueva"),
            {
                "fecha_hora": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "actividad": self.ciiu.pk,
                "valor": "0",
                "tipo_cliente": "particular",
            },
        )
        self.assertEqual(Venta.objects.count(), 1)

    def test_rf03_registrar_compra(self):
        self._login("carlos.ruiz")
        resp = self.client.post(
            reverse("compra_nueva"),
            {
                "fecha": self.hoy.isoformat(),
                "valor": "80000",
                "proveedor": "Abarrotes Boyacá",
                "concepto": "Mercancía",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Compra.objects.get().proveedor, "Abarrotes Boyacá")

    def test_rf04_retencion_en_venta_a_empresa(self):
        self._login("maria.gomez")
        self.client.post(
            reverse("venta_nueva"),
            {
                "fecha_hora": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "actividad": self.ciiu.pk,
                "motivo": self.motivo.pk,
                "valor": "1500000",
                "tipo_cliente": "empresa",
                "tercero": "Cliente mayorista SAS",
                "retencion_tipo": "ica",
                "retencion_valor": "12400",
            },
        )
        ret = Retencion.objects.get()
        self.assertEqual(ret.valor, Decimal("12400"))
        self.assertEqual(ret.tercero, "Cliente mayorista SAS")
        self.assertEqual(ret.venta.tipo_cliente, "empresa")

    def test_rf05_historial_filtra_por_tipo_y_fechas(self):
        self._login()
        Venta.objects.create(
            establecimiento=self.est,
            usuario=self.maria,
            actividad=self.ciiu,
            fecha=self.hoy,
            valor=Decimal("10000"),
            concepto="Venta",
        )
        Compra.objects.create(
            establecimiento=self.est,
            usuario=self.maria,
            fecha=self.hoy,
            valor=Decimal("5000"),
            proveedor="Abarrotes Boyacá",
        )
        resp = self.client.get(reverse("historial"), {"tipo": "venta"})
        self.assertContains(resp, "Venta")
        self.assertNotContains(resp, "Abarrotes Boyacá")

        manana = (self.hoy + timedelta(days=1)).isoformat()
        resp = self.client.get(
            reverse("historial"),
            {"desde": manana, "hasta": self.hoy.isoformat()},
        )
        self.assertContains(resp, "no puede ser menor")

    def test_rf06_editar_deja_auditoria_y_dependiente_no_edita(self):
        venta = Venta.objects.create(
            establecimiento=self.est,
            usuario=self.carlos,
            actividad=self.ciiu,
            fecha=self.hoy,
            valor=Decimal("10000"),
            concepto="Venta",
        )
        self._login("carlos.ruiz")
        resp = self.client.post(
            reverse("editar", args=["venta", venta.pk]),
            {"valor": "20000", "motivo_cambio": "corrección"},
        )
        self.assertEqual(resp.status_code, 302)
        venta.refresh_from_db()
        self.assertEqual(venta.valor, Decimal("10000"))

        self.client.logout()
        self._login("maria.gomez")
        resp = self.client.post(
            reverse("editar", args=["venta", venta.pk]),
            {
                "fecha_hora": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "actividad": self.ciiu.pk,
                "motivo": self.motivo.pk,
                "valor": "20000",
                "tipo_cliente": "particular",
                "motivo_cambio": "corrección de valor",
            },
        )
        self.assertEqual(resp.status_code, 302)
        venta.refresh_from_db()
        self.assertEqual(venta.valor, Decimal("20000"))
        audit = Auditoria.objects.get(accion="editar")
        self.assertIn("10000", audit.valor_anterior)
        self.assertIn("20000", audit.valor_nuevo)
        self.assertEqual(audit.motivo, "corrección de valor")

    def test_rf07_anular_es_logico_y_pide_motivo(self):
        venta = Venta.objects.create(
            establecimiento=self.est,
            usuario=self.maria,
            actividad=self.ciiu,
            fecha=self.hoy,
            valor=Decimal("1500000"),
            concepto="Venta",
            tipo_cliente="empresa",
        )
        Retencion.objects.create(
            establecimiento=self.est,
            usuario=self.maria,
            venta=venta,
            fecha=self.hoy,
            tipo="ica",
            valor=Decimal("12400"),
            tercero="Mayorista",
        )
        self._login()
        resp = self.client.post(reverse("anular", args=["venta", venta.pk]), {})
        venta.refresh_from_db()
        self.assertEqual(venta.estado, "vigente")

        resp = self.client.post(
            reverse("anular", args=["venta", venta.pk]),
            {"motivo": "digitó mal el valor"},
        )
        self.assertEqual(resp.status_code, 302)
        venta.refresh_from_db()
        self.assertEqual(venta.estado, "anulado")
        self.assertTrue(Venta.objects.filter(pk=venta.pk).exists())
        self.assertEqual(Retencion.objects.get().estado, "anulado")
        self.assertTrue(Auditoria.objects.filter(accion="anular", motivo="digitó mal el valor").exists())

    def test_rf08_consolidado_solo_suma_vigentes(self):
        Venta.objects.create(
            establecimiento=self.est,
            usuario=self.maria,
            actividad=self.ciiu,
            fecha=self.hoy,
            valor=Decimal("100000"),
            estado="vigente",
        )
        Venta.objects.create(
            establecimiento=self.est,
            usuario=self.maria,
            actividad=self.ciiu,
            fecha=self.hoy,
            valor=Decimal("50000"),
            estado="anulado",
        )
        Compra.objects.create(
            establecimiento=self.est,
            usuario=self.maria,
            fecha=self.hoy,
            valor=Decimal("20000"),
            proveedor="X",
        )
        self._login()
        resp = self.client.get(reverse("inicio"))
        self.assertContains(resp, "100.000")
        self.assertContains(resp, "20.000")
        self.assertContains(resp, "Base neta estimada")

    def test_rf09_configurar_correo(self):
        self._login()
        resp = self.client.post(
            reverse("configuracion"),
            {"accion": "correo", "correo_reportes": "nuevo@contador.com"},
        )
        self.assertEqual(resp.status_code, 302)
        self.est.refresh_from_db()
        self.assertEqual(self.est.correo_reportes, "nuevo@contador.com")

        self.client.logout()
        self._login("carlos.ruiz")
        resp = self.client.get(reverse("configuracion"))
        self.assertEqual(resp.status_code, 302)

    def test_rf10_enviar_reporte_y_descargar(self):
        Venta.objects.create(
            establecimiento=self.est,
            usuario=self.maria,
            actividad=self.ciiu,
            fecha=self.hoy,
            valor=Decimal("100000"),
        )
        self._login()
        resp = self.client.post(
            reverse("enviar_reporte"),
            {"desde": self.hoy.isoformat(), "hasta": self.hoy.isoformat()},
        )
        self.assertEqual(resp.status_code, 302)
        envio = EnvioReporte.objects.get()
        self.assertEqual(envio.estado_envio, "enviado")
        self.assertEqual(envio.total_ingresos, Decimal("100000"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("100", mail.outbox[0].body)

        pdf = self.client.get(
            reverse("enviar_reporte"),
            {"desde": self.hoy.isoformat(), "hasta": self.hoy.isoformat(), "formato": "pdf"},
        )
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        csv_resp = self.client.get(
            reverse("enviar_reporte"),
            {"desde": self.hoy.isoformat(), "hasta": self.hoy.isoformat(), "formato": "csv"},
        )
        self.assertIn("text/csv", csv_resp["Content-Type"])

    def test_propietario_crea_y_desactiva_dependiente(self):
        self._login()
        resp = self.client.post(
            reverse("configuracion"),
            {
                "accion": "usuario",
                "first_name": "Ana",
                "last_name": "López",
                "username": "ana.lopez",
                "password": "tunja2026",
                "rol": "dependiente",
            },
        )
        self.assertEqual(resp.status_code, 302)
        ana = User.objects.get(username="ana.lopez")
        perfil = Perfil.objects.get(user=ana)
        self.assertEqual(perfil.establecimiento, self.est)
        self.client.post(
            reverse("configuracion"),
            {"accion": "desactivar", "user_id": str(perfil.pk)},
        )
        ana.refresh_from_db()
        self.assertFalse(ana.is_active)

    def test_filtros_periodo_rapido_y_usuario_en_historial(self):
        self._login()
        Venta.objects.create(
            establecimiento=self.est,
            usuario=self.maria,
            fecha=self.hoy,
            valor=Decimal("50000"),
            concepto="Venta Maria",
        )
        Venta.objects.create(
            establecimiento=self.est,
            usuario=self.carlos,
            fecha=self.hoy,
            valor=Decimal("30000"),
            concepto="Venta Carlos",
        )
        # Acceso con periodo rápido
        resp_mes = self.client.get(reverse("inicio"), {"periodo": "este_mes"})
        self.assertEqual(resp_mes.status_code, 200)

        # Filtro por usuario específico en historial
        resp_carlos = self.client.get(reverse("historial"), {"usuario": str(self.carlos.pk)})
        self.assertEqual(resp_carlos.status_code, 200)
        self.assertEqual(len(resp_carlos.context["filas"]), 1)
        self.assertEqual(resp_carlos.context["filas"][0]["obj"].concepto, "Venta Carlos")
        self.assertEqual(resp_carlos.context["ingresos"], Decimal("30000"))
