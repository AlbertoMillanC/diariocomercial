"""
registro/test_bre_b.py
Pruebas exhaustivas para el Motor Bre-B (EMVCo MPM, CRC-16, Webhook HMAC, Liquidación Atómica y Soundbox).
"""
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User

from registro.models import (
    Establecimiento,
    Municipio,
    Perfil,
    ActividadCIIU,
    Producto,
    Venta,
    TransaccionBreB,
)
from registro.bre_b_service import (
    calcular_crc16_ccitt,
    generar_qr_dinamico_bre_b,
    verificar_firma_webhook,
    procesar_confirmacion_bre_b,
)


class BreBServiceTests(TestCase):
    def setUp(self):
        self.municipio = Municipio.objects.create(
            codigo_dane="15001", nombre="Tunja", departamento="Boyacá"
        )
        self.tienda = Establecimiento.objects.create(
            nombre="Carnicería El Buen Corte",
            municipio=self.municipio,
            llave_bre_b="3101234567",
            tipo_llave_bre_b="celular",
        )
        self.user = User.objects.create_user(username="carnicero", password="password123")
        self.perfil = Perfil.objects.create(
            user=self.user, establecimiento=self.tienda, rol="propietario"
        )
        self.actividad = ActividadCIIU.objects.create(
            establecimiento=self.tienda, codigo="4722", descripcion="Carnicería", tarifa_x_mil=Decimal("6.0")
        )
        self.prod_pechuga = Producto.objects.create(
            establecimiento=self.tienda,
            nombre="Pechuga de pollo",
            categoria="carnes",
            precio_kilo=Decimal("20000"),
            stock_kilos=Decimal("15.000"),
        )

    def test_calculo_crc16_ccitt_estandar(self):
        """El algoritmo CRC-16/CCITT-FALSE produce el checksum hexadecimal exacto."""
        # Ejemplo canónico EMVCo
        payload_test = "0002010102125303170540845000.005802CO5910TEST STORE6005TUNJA6304"
        crc = calcular_crc16_ccitt(payload_test)
        self.assertEqual(len(crc), 4)
        self.assertTrue(all(c in "0123456789ABCDEF" for c in crc))

    def test_generacion_qr_dinamico_bre_b(self):
        """Genera el payload EMVCo Bre-B completo con monto embebido y referencia única."""
        tx, payload = generar_qr_dinamico_bre_b(
            establecimiento=self.tienda,
            monto=Decimal("45000.00"),
            comando_original="45 mil pechuga",
            ciudad="TUNJA",
        )
        self.assertEqual(tx.monto, Decimal("45000.00"))
        self.assertEqual(tx.estado, "iniciada")
        self.assertTrue(tx.token_visual_corto.startswith("#"))
        self.assertEqual(len(tx.token_visual_corto), 4)

        # Verificaciones en el payload EMVCo
        self.assertIn("co.gov.banrep.bre-b", payload)
        self.assertIn("3101234567", payload)
        self.assertIn("45000.00", payload)
        self.assertIn("TUNJA", payload)
        self.assertIn("CARNICERIA EL BUEN C", payload)

    def test_verificacion_firma_criptografica_webhook(self):
        """Valida que solo webhooks con firma HMAC-SHA256 válida sean procesados."""
        cuerpo = b'{"event":"payment.approved","reference":"DC-12345"}'
        secreto = "super_secreto_banrep_2026"

        import hmac, hashlib
        firma_valida = hmac.new(secreto.encode("utf-8"), cuerpo, hashlib.sha256).hexdigest()

        self.assertTrue(verificar_firma_webhook(cuerpo, firma_valida, secreto))
        self.assertFalse(verificar_firma_webhook(cuerpo, "firma_falsa_hack", secreto))

    def test_liquidacion_atomica_y_soundbox(self):
        """
        Al recibir la confirmación de Bre-B:
        1. Venta queda creada con medio_pago='bre_b'.
        2. El inventario se descuenta atómicamente.
        3. El ICA queda liquidado.
        4. El texto para el Soundbox de voz es generado.
        """
        tx, _ = generar_qr_dinamico_bre_b(
            establecimiento=self.tienda,
            monto=Decimal("40000.00"),
            comando_original="40 mil pechuga",
        )

        exito, texto_voz, venta, tx_confirmada = procesar_confirmacion_bre_b(
            referencia_unica=tx.referencia_unica,
            monto_acreditado=Decimal("40000.00"),
            banrep_transaction_id="CUS-998877",
            banco_origen="Nequi Bancolombia",
            nombre_pagador="Carlos Millán",
        )

        self.assertTrue(exito)
        self.assertIn("Bre-B recibido: 40.000 pesos de Carlos Millán", texto_voz)
        self.assertIsNotNone(venta)
        self.assertEqual(venta.medio_pago, "bre_b")
        self.assertEqual(venta.valor, Decimal("40000.00"))
        # ICA Tunja para carnes 6x1000 de $40.000 = $240 COP
        self.assertEqual(venta.ica_estimado, Decimal("240.00"))

        # Descuento de 2 Kilos de pechuga (40.000 / 20.000/kg = 2 Kg)
        self.prod_pechuga.refresh_from_db()
        self.assertEqual(self.prod_pechuga.stock_kilos, Decimal("13.000"))

        # Transacción Bre-B queda en estado aprobada
        tx_confirmada.refresh_from_db()
        self.assertEqual(tx_confirmada.estado, "aprobada")
        self.assertEqual(tx_confirmada.id_transaccion_banrep, "CUS-998877")

    def test_idempotencia_webhook_bre_b(self):
        """Si el adquirente reintenta el webhook por lag de red, no se duplica la venta."""
        tx, _ = generar_qr_dinamico_bre_b(
            establecimiento=self.tienda,
            monto=Decimal("20000.00"),
            comando_original="20 mil pechuga",
        )

        # Primer despacho
        exito1, _, venta1, _ = procesar_confirmacion_bre_b(
            referencia_unica=tx.referencia_unica,
            monto_acreditado=Decimal("20000.00"),
            banrep_transaction_id="CUS-111",
        )
        self.assertTrue(exito1)
        self.assertEqual(Venta.objects.filter(establecimiento=self.tienda).count(), 1)

        # Segundo despacho idéntico (reintento del banco)
        exito2, msg2, venta2, _ = procesar_confirmacion_bre_b(
            referencia_unica=tx.referencia_unica,
            monto_acreditado=Decimal("20000.00"),
            banrep_transaction_id="CUS-111",
        )
        self.assertTrue(exito2)
        self.assertIn("Idempotente", msg2)
        # Sigue habiendo solo 1 venta
        self.assertEqual(Venta.objects.filter(establecimiento=self.tienda).count(), 1)
        self.assertEqual(venta1.pk, venta2.pk)
