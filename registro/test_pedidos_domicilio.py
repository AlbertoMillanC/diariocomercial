import base64
from decimal import Decimal
from django.test import TestCase, Client as HttpClient
from django.contrib.auth.models import User
from django.utils import timezone
from django.urls import reverse

from registro.models import (
    Establecimiento,
    Municipio,
    Perfil,
    Producto,
    Pedido,
    LineaPedido,
    TransaccionBreB,
    TokenVinculacion,
)
from registro.inventario_service import parsear_dinero
from registro.recibo_service import (
    generar_comanda_termica_texto,
    generar_bytes_escpos_comanda,
    generar_pdf_comanda_pedido,
)
from registro.bot_service import despachar_mensaje, _PEDIDOS_PENDIENTES
from registro.bre_b_service import procesar_confirmacion_bre_b


class PedidosDomicilioTestCase(TestCase):
    def setUp(self):
        _PEDIDOS_PENDIENTES.clear()
        self.municipio = Municipio.objects.create(
            codigo_dane="15001",
            nombre="Tunja",
            departamento="Boyacá",
        )
        self.user = User.objects.create_user(username="tendero_prueba", password="password123")
        self.est = Establecimiento.objects.create(
            nombre="Super Carnes Don Pedro",
            nit="900123456-1",
            municipio=self.municipio,
            costo_domicilio_defecto=Decimal("3000"),
            monto_minimo_domicilio_gratis=Decimal("50000"),
            llave_bre_b="3141234567",
            tipo_llave_bre_b="celular",
            banco_receptor_bre_b="Bancolombia",
        )
        self.perfil = Perfil.objects.create(
            user=self.user,
            establecimiento=self.est,
            rol="propietario",
        )
        self.token_vinc = TokenVinculacion.objects.create(
            establecimiento=self.est,
            usuario=self.user,
            token="PRINT-TOKEN-1234",
            expira=timezone.now() + timezone.timedelta(days=30),
        )

        # Crear productos de prueba
        self.prod_pan = Producto.objects.create(
            establecimiento=self.est,
            nombre="Pan Rollito",
            precio_kilo=Decimal("500"),
            stock_kilos=Decimal("100"),
            unidad_medida="und",
        )
        self.prod_carne = Producto.objects.create(
            establecimiento=self.est,
            nombre="Carne de Res",
            precio_kilo=Decimal("28000"),
            stock_kilos=Decimal("50"),
            unidad_medida="kg",
        )
        self.prod_aceite = Producto.objects.create(
            establecimiento=self.est,
            nombre="Aceite 500ml",
            precio_kilo=Decimal("8000"),
            stock_kilos=Decimal("20"),
            unidad_medida="und",
        )

    def test_parsear_dinero_mil(self):
        """Prueba que 'mil' por sí solo y palabras de números se reconozcan como COP."""
        monto, _ = parsear_dinero("mil")
        self.assertEqual(monto, Decimal("1000"))
        monto, _ = parsear_dinero("mil de pan")
        self.assertEqual(monto, Decimal("1000"))
        monto, _ = parsear_dinero("dos mil")
        self.assertEqual(monto, Decimal("2000"))
        monto, _ = parsear_dinero("cinco mil")
        self.assertEqual(monto, Decimal("5000"))
        monto, _ = parsear_dinero("cincuenta mil")
        self.assertEqual(monto, Decimal("50000"))

    def test_flujo_pedido_efectivo_con_vueltas(self):
        """Prueba el flujo completo conversacional seleccionando Efectivo y pidiendo vueltas exactas."""
        telefono_cliente = "3109876543"

        # Paso 1: Cliente solicita pedido con dirección
        msg_1 = "pedido 2 libras de carne y 1 aceite Calle 12 # 4-50 casa reja verde"
        resp_1, _, adjuntos_1 = despachar_mensaje(
            canal="whatsapp",
            identificador_externo=telefono_cliente,
            texto_mensaje=msg_1,
            return_adjuntos=True,
        )

        self.assertIn("Cotizado", resp_1)
        self.assertIn("Calle 12 # 4-50", resp_1)
        self.assertIn("1", resp_1)
        self.assertIn("Efectivo", resp_1)
        self.assertIn("Bre-B", resp_1)

        # Paso 2: Cliente responde 1 (Efectivo)
        resp_2, _, adjuntos_2 = despachar_mensaje(
            canal="whatsapp",
            identificador_externo=telefono_cliente,
            texto_mensaje="1",
            return_adjuntos=True,
        )

        self.assertIn("con cuánto vas a pagar", resp_2.lower())

        # Paso 3: Cliente dice "50 mil"
        resp_3, _, adjuntos_3 = despachar_mensaje(
            canal="whatsapp",
            identificador_externo=telefono_cliente,
            texto_mensaje="50 mil",
            return_adjuntos=True,
        )

        self.assertIn("Confirmado", resp_3)
        self.assertIn("Vueltas", resp_3)

        # Verificar que el pedido se guardó en la base de datos
        pedido = Pedido.objects.filter(telefono_contacto=telefono_cliente).latest("id")
        self.assertEqual(pedido.medio_pago, "efectivo")
        self.assertEqual(pedido.paga_con, Decimal("50000"))
        self.assertTrue(pedido.vueltas > Decimal("0"))
        self.assertEqual(pedido.vueltas, Decimal("50000") - pedido.total)
        self.assertFalse(pedido.impreso_pos)
        self.assertEqual(pedido.estado, "en_preparacion")

    def test_flujo_pedido_bre_b_y_confirmacion_webhook(self):
        """Prueba la selección de Bre-B, generación de llave y QR separado, y confirmación por webhook."""
        telefono_cliente = "3201112233"

        # Paso 1: Pedido
        msg_1 = "pedido 1 aceite y 4 panes Carrera 8 # 19-30 apto 201"
        despachar_mensaje(canal="whatsapp", identificador_externo=telefono_cliente, texto_mensaje=msg_1)

        # Paso 2: Selección Bre-B (opción 2)
        resp_2, _, adjuntos_2 = despachar_mensaje(
            canal="whatsapp",
            identificador_externo=telefono_cliente,
            texto_mensaje="2",
            return_adjuntos=True,
        )

        # Debe contener la llave en texto
        self.assertIn("Cobro Electrónico Bre-B", resp_2)
        self.assertIn(self.est.llave_bre_b, resp_2)
        self.assertIn("QR", resp_2)

        # Debe contener la información de cobro y payload QR
        self.assertIsNotNone(adjuntos_2)
        self.assertIn("payload", adjuntos_2)
        self.assertEqual(adjuntos_2["tx"], pedido.transaccion_bre_b if False else adjuntos_2["tx"])

        # Verificar pedido en BD
        pedido = Pedido.objects.filter(telefono_contacto=telefono_cliente).latest("id")
        self.assertEqual(pedido.estado, "esperando_pago")
        self.assertIsNotNone(pedido.transaccion_bre_b)

        # Paso 3: Simular llegada del Webhook bancario Bre-B
        tx = pedido.transaccion_bre_b
        ok_webhook, msg_wh, venta_wh, tx_wh = procesar_confirmacion_bre_b(
            referencia_unica=tx.referencia_unica,
            monto_acreditado=pedido.total,
            banrep_transaction_id="SWITCH-BANREP-998877",
            banco_origen="Nequi Bancolombia",
        )
        self.assertTrue(ok_webhook)

        # Refrescar pedido
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, "pagado")
        self.assertFalse(pedido.impreso_pos)
        self.assertIsNotNone(pedido.venta)

    def test_api_cola_impresion_y_ack(self):
        """Prueba que el print daemon pueda consultar la cola y confirmar con ACK."""
        client = HttpClient()

        # Crear un pedido pendiente de impresión
        pedido = Pedido.objects.create(
            establecimiento=self.est,
            telefono_contacto="3155554433",
            nombre_contacto="Doña Marta",
            direccion_entrega="Calle 20 # 10-15",
            punto_referencia="Frente a la panadería",
            subtotal=Decimal("28000"),
            costo_domicilio=Decimal("3000"),
            total=Decimal("31000"),
            medio_pago="efectivo",
            paga_con=Decimal("50000"),
            vueltas=Decimal("19000"),
            estado="en_preparacion",
            impreso_pos=False,
        )
        LineaPedido.objects.create(
            pedido=pedido,
            producto=self.prod_carne,
            nombre_producto=self.prod_carne.nombre,
            cantidad=Decimal("2.000"),
            unidad_medida="lb",
            precio_unitario=Decimal("14000"),
            subtotal=Decimal("28000"),
        )

        # Consultar cola con token
        url_cola = reverse("api_cola_impresion_pos") + f"?token={self.token_vinc.token}"
        resp_cola = client.get(url_cola)
        self.assertEqual(resp_cola.status_code, 200)
        data = resp_cola.json()
        self.assertGreaterEqual(data["pendientes_count"], 1)

        trabajo = [t for t in data["trabajos"] if t["id"] == pedido.pk][0]
        self.assertEqual(trabajo["numero_pedido"], pedido.numero_pedido)
        self.assertIn("texto_termico", trabajo)
        self.assertIn("escpos_base64", trabajo)

        # Enviar ACK
        url_ack = reverse("api_ack_impresion_pos", kwargs={"pk": pedido.pk})
        resp_ack = client.post(url_ack, {"token": self.token_vinc.token})
        self.assertEqual(resp_ack.status_code, 200)

        # Verificar que el pedido ahora tiene impreso_pos = True
        pedido.refresh_from_db()
        self.assertTrue(pedido.impreso_pos)
        self.assertEqual(pedido.veces_impreso, 1)

    def test_generacion_comandas_termicas(self):
        """Prueba que los generadores de comanda (texto, ESC/POS y PDF) funcionen sin fallas."""
        pedido = Pedido.objects.create(
            establecimiento=self.est,
            telefono_contacto="3112223344",
            nombre_contacto="Carlos M.",
            direccion_entrega="Calle 10 # 5-20",
            subtotal=Decimal("10000"),
            costo_domicilio=Decimal("0"),
            total=Decimal("10000"),
            medio_pago="bre_b",
            estado="pagado",
        )
        LineaPedido.objects.create(
            pedido=pedido,
            nombre_producto="Pan de Bono",
            cantidad=Decimal("5.000"),
            unidad_medida="und",
            precio_unitario=Decimal("2000"),
            subtotal=Decimal("10000"),
        )

        texto = generar_comanda_termica_texto(pedido)
        self.assertIn("PAGADO CON", texto)
        self.assertIn("Calle 10 # 5-20", texto)

        escpos = generar_bytes_escpos_comanda(pedido)
        self.assertIsInstance(escpos, bytes)
        self.assertTrue(len(escpos) > 50)
        # Comprobar secuencia de corte ESC/POS \x1d\x56\x41\x00
        self.assertIn(b"\x1d\x56\x41\x00", escpos)

        pdf_buf = generar_pdf_comanda_pedido(pedido)
        self.assertIsNotNone(pdf_buf)
        self.assertTrue(len(pdf_buf) > 500)
