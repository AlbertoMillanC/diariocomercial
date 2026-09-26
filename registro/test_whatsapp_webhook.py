import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from registro.models import Establecimiento, Municipio, Perfil, Producto, Venta, VinculoCanal


class WhatsAppWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.mun = Municipio.objects.create(nombre="Tunja", codigo_dane="15001")
        self.est = Establecimiento.objects.create(
            nombre="Super Carnes WhatsApp",
            nit="901999888-2",
            municipio=self.mun,
        )
        self.user = User.objects.create_user(
            username="don_ramon_wa",
            password="password123",
        )
        self.perfil = Perfil.objects.create(
            user=self.user,
            establecimiento=self.est,
            rol="propietario",
        )
        self.prod_carne = Producto.objects.create(
            establecimiento=self.est,
            nombre="Carne molida",
            categoria="carnes",
            unidad_medida="kg",
            precio_kilo=Decimal("25000"),
            stock_kilos=Decimal("50.0"),
        )
        # Vincular número de WhatsApp 573146922087
        self.telefono = "573146922087"
        self.vinculo = VinculoCanal.objects.create(
            canal="whatsapp",
            identificador_externo=self.telefono,
            usuario=self.user,
            establecimiento=self.est,
            activo=True,
        )

    def test_meta_webhook_verification_handshake_get(self):
        """Prueba el handshake GET de verificación de Meta Cloud API con hub.challenge."""
        # 1. Token correcto
        resp = self.client.get("/webhook/whatsapp/", {
            "hub.mode": "subscribe",
            "hub.verify_token": "diariocomercial_token_2026",
            "hub.challenge": "CHALLENGE_ACCEPTED_12345",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode("utf-8"), "CHALLENGE_ACCEPTED_12345")

        # 2. Token incorrecto -> 403 Forbidden
        resp_bad = self.client.get("/webhook/whatsapp/", {
            "hub.mode": "subscribe",
            "hub.verify_token": "token_falso_hack",
            "hub.challenge": "CHALLENGE_ACCEPTED_12345",
        })
        self.assertEqual(resp_bad.status_code, 403)

    def test_meta_webhook_incoming_message_post_sale(self):
        """Simula payload JSON entrante de Meta Webhook registrando una venta de 40 mil carne."""
        payload_meta = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15550239999",
                                    "phone_number_id": "1234567890",
                                },
                                "contacts": [{"profile": {"name": "Don Ramon"}, "wa_id": self.telefono}],
                                "messages": [
                                    {
                                        "from": self.telefono,
                                        "id": "wamid.HBgLM...",
                                        "timestamp": "1727376000",
                                        "text": {"body": "40 mil carne nequi"},
                                        "type": "text",
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        resp = self.client.post(
            "/webhook/whatsapp/",
            data=json.dumps(payload_meta),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode("utf-8"), "EVENT_RECEIVED")

        # Verificar que la venta se guardó en la base de datos
        v = Venta.objects.filter(establecimiento=self.est).latest("id")
        self.assertEqual(v.valor, Decimal("40000"))
        self.assertEqual(v.medio_pago, "nequi")
        self.assertEqual(v.cantidad, Decimal("1600.000"))
        self.assertEqual(v.unidad_medida, "g")
        self.assertIn("1.600 g", v.concepto)

        # Verificar descuento de inventario
        self.prod_carne.refresh_from_db()
        self.assertEqual(self.prod_carne.stock_kilos, Decimal("48.400"))

    def test_bloqueo_venta_sin_stock(self):
        """Verifica que un producto con stock 0 no se pueda vender ni cobrar."""
        from registro.bot_service import despachar_mensaje
        from registro.models import Producto

        prod_pan = Producto.objects.create(
            establecimiento=self.est,
            nombre="Pan rollo artesanal",
            categoria="panaderia",
            unidad_medida="und",
            precio_kilo=Decimal("500"),
            stock_kilos=Decimal("0"),
        )

        resp, venta, _ = despachar_mensaje("whatsapp", "573146922087", "2 mil de pan", return_adjuntos=True)
        self.assertIsNone(venta)
        self.assertIn("Venta rechazada", resp)
        self.assertIn("AGOTADO", resp)

    def test_nit_31_dispara_proceso_factura_electronica(self):
        """Verifica que el código DIAN 31 (NIT) inicia el proceso de Factura Electrónica."""
        from registro.bot_service import despachar_mensaje

        # Paso 1: Pide venta con 31 (NIT)
        resp1, v1, _ = despachar_mensaje("whatsapp", "573146922087", "40 mil carne 31", return_adjuntos=True)
        self.assertIsNone(v1)
        self.assertIn("Requiere Factura Electrónica DIAN", resp1)
        self.assertIn("NIT", resp1)

        # Paso 2: Responde con datos completos (NIT, Razón Social y Correo)
        resp2, v2, _ = despachar_mensaje(
            "whatsapp", "573146922087", "900123456-1 Empresa Boyaca contabilidad@empresa.com", return_adjuntos=True
        )
        self.assertIsNotNone(v2)
        self.assertTrue(v2.solicita_factura_electronica)
        self.assertEqual(v2.estado_dian, "aprobada")
        self.assertEqual(v2.cliente.tipo_documento, "31")
        self.assertEqual(v2.cliente.nit_cedula, "900123456-1")
        self.assertIn("Factura Electrónica Emitida", resp2)
        self.assertIn("FE-", resp2)

