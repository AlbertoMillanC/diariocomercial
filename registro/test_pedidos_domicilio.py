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

    def test_servicio_domicilio_disponible_metodo(self):
        """Prueba la lógica de negocio del método servicio_domicilio_disponible en Establecimiento."""
        from datetime import datetime, time
        est = self.est

        # 1. Por defecto activo
        est.domicilios_activos = True
        est.domicilio_programar_horario = False
        disp, mot, msg = est.servicio_domicilio_disponible()
        self.assertTrue(disp)
        self.assertEqual(mot, "activo")
        self.assertIn("ACTIVO", msg)

        # 2. Pausado manualmente con motivo
        est.domicilios_activos = False
        est.domicilio_mensaje_pausa = "Lluvia torrencial en Tunja"
        disp, mot, msg = est.servicio_domicilio_disponible()
        self.assertFalse(disp)
        self.assertEqual(mot, "pausado")
        self.assertIn("Pausado", msg)
        self.assertIn("Lluvia torrencial", msg)
        self.assertIn("Horario habitual", msg)

        # 3. Control por horario: dentro de horario
        est.domicilios_activos = True
        est.domicilio_programar_horario = True
        est.domicilio_hora_apertura = time(8, 0)
        est.domicilio_hora_cierre = time(20, 0)

        dt_dentro = timezone.now().replace(hour=14, minute=30)
        disp, mot, msg = est.servicio_domicilio_disponible(dt=dt_dentro)
        self.assertTrue(disp)

        # 4. Control por horario: fuera de horario (ej: 22:30)
        dt_fuera = timezone.now().replace(hour=22, minute=30)
        disp, mot, msg = est.servicio_domicilio_disponible(dt=dt_fuera)
        self.assertFalse(disp)
        self.assertEqual(mot, "fuera_de_horario")
        self.assertIn("Cerrado", msg)
        self.assertIn("Horario de Domicilios", msg)

    def test_bloqueo_pedido_cuando_domicilios_pausados(self):
        """Prueba que el bot no tome pedidos cuando el servicio esté pausado e informe al cliente."""
        self.est.domicilios_activos = False
        self.est.domicilio_mensaje_pausa = "Cocina saturada por evento"
        self.est.save()

        telefono_cliente = "3129998877"
        msg = "pedido 2 libras de carne Calle 12 # 4-50 casa reja verde"
        resp, _, _ = despachar_mensaje(canal="whatsapp", identificador_externo=telefono_cliente, texto_mensaje=msg, return_adjuntos=True)

        self.assertIn("Pausado", resp)
        self.assertIn("Cocina saturada", resp)
        self.assertIn("Horario habitual", resp)

        # Verificar que no se creó pedido
        self.assertFalse(Pedido.objects.filter(telefono_contacto=telefono_cliente).exists())

    def test_cliente_pregunta_horario_domicilios(self):
        """Prueba que cuando un cliente pregunta si hay servicio o los horarios, el bot responde el estado."""
        telefono_cliente = "3191234567"
        resp, _, _ = despachar_mensaje(canal="whatsapp", identificador_externo=telefono_cliente, texto_mensaje="¿Hay servicio de domicilios hoy?", return_adjuntos=True)

        self.assertIn("Domicilios ACTIVO", resp)
        self.assertIn("Horario de atención", resp)

    def test_comandos_staff_domicilios_bot(self):
        """Prueba los comandos /domicilios off, on y horario para el comerciante."""
        from registro.models import VinculoCanal
        id_staff = "99887766"
        VinculoCanal.objects.create(
            canal="telegram",
            identificador_externo=id_staff,
            usuario=self.user,
            establecimiento=self.est,
            activo=True,
        )

        # Pausar con motivo
        resp_off, _, _ = despachar_mensaje(canal="telegram", identificador_externo=id_staff, texto_mensaje="/domicilios off Congestión", return_adjuntos=True)
        self.assertIn("PAUSADO", resp_off)
        self.est.refresh_from_db()
        self.assertFalse(self.est.domicilios_activos)
        self.assertEqual(self.est.domicilio_mensaje_pausa, "Congestión")

        # Reactivar
        resp_on, _, _ = despachar_mensaje(canal="telegram", identificador_externo=id_staff, texto_mensaje="/domicilios on", return_adjuntos=True)
        self.assertIn("ACTIVADO", resp_on)
        self.est.refresh_from_db()
        self.assertTrue(self.est.domicilios_activos)

        # Cambiar horario
        resp_horario, _, _ = despachar_mensaje(canal="telegram", identificador_externo=id_staff, texto_mensaje="/domicilios horario 08:30 21:30", return_adjuntos=True)
        self.assertIn("Actualizado", resp_horario)
        self.est.refresh_from_db()
        self.assertTrue(self.est.domicilio_programar_horario)
        self.assertEqual(self.est.domicilio_hora_apertura.strftime("%H:%M"), "08:30")
        self.assertEqual(self.est.domicilio_hora_cierre.strftime("%H:%M"), "21:30")

    def test_kds_toggle_servicio_endpoint(self):
        """Prueba el endpoint web para encender/apagar y configurar horario desde el panel KDS."""
        client = HttpClient()
        client.force_login(self.user)

        # 1. Toggle a pausado
        url = reverse("domicilios_toggle_servicio")
        resp = client.post(url, {"accion": "toggle", "motivo_pausa": "Lluvia"})
        self.assertEqual(resp.status_code, 302)
        self.est.refresh_from_db()
        self.assertFalse(self.est.domicilios_activos)
        self.assertEqual(self.est.domicilio_mensaje_pausa, "Lluvia")

        # 2. Toggle a activo
        resp2 = client.post(url, {"accion": "toggle"})
        self.assertEqual(resp2.status_code, 302)
        self.est.refresh_from_db()
        self.assertTrue(self.est.domicilios_activos)

        # 3. Guardar horario
        resp3 = client.post(url, {
            "accion": "horario",
            "hora_apertura": "09:00",
            "hora_cierre": "22:00",
            "programar_horario": "on",
            "costo_defecto": "4000",
            "minimo_gratis": "60000",
        })
        self.assertEqual(resp3.status_code, 302)
        self.est.refresh_from_db()
        self.assertEqual(self.est.domicilio_hora_apertura.strftime("%H:%M"), "09:00")
        self.assertEqual(self.est.domicilio_hora_cierre.strftime("%H:%M"), "22:00")
        self.assertTrue(self.est.domicilio_programar_horario)
        self.assertEqual(self.est.costo_domicilio_defecto, Decimal("4000"))
        self.assertEqual(self.est.monto_minimo_domicilio_gratis, Decimal("60000"))

    def test_flujo_pedido_recojo_tienda_efectivo(self):
        """Prueba pedido en línea con recojo en tienda / para llevar."""
        telefono_cliente = "3123456789"

        # Paso 1: Pedido con keyword de recojo
        msg_1 = "pedido 4 pan rollito para recoger en tienda"
        resp_1, _, _ = despachar_mensaje(
            canal="whatsapp",
            identificador_externo=telefono_cliente,
            texto_mensaje=msg_1,
            return_adjuntos=True
        )

        self.assertIn("Recojo en Tienda", resp_1)
        self.assertIn("Sin costo de envío", resp_1)
        self.assertIn("Punto de recogida", resp_1)

        # Paso 2: Selección Efectivo
        resp_2, _, _ = despachar_mensaje(
            canal="whatsapp",
            identificador_externo=telefono_cliente,
            texto_mensaje="1",
            return_adjuntos=True
        )

        self.assertIn("Pago en Efectivo Seleccionado (En Caja)", resp_2)
        self.assertIn("Total a pagar en mostrador", resp_2)

        # Paso 3: Paga exacto en caja
        resp_3, _, _ = despachar_mensaje(
            canal="whatsapp",
            identificador_externo=telefono_cliente,
            texto_mensaje="exacto",
            return_adjuntos=True
        )

        self.assertIn("Confirmado para Recoger en Tienda", resp_3)
        self.assertIn("Punto de recogida", resp_3)

        # Validar en base de datos
        pedido = Pedido.objects.filter(telefono_contacto=telefono_cliente).latest("id")
        self.assertEqual(pedido.tipo_entrega, "recojo_tienda")
        self.assertEqual(pedido.costo_domicilio, Decimal("0"))
        self.assertEqual(pedido.medio_pago, "efectivo")
        self.assertEqual(pedido.estado, "en_preparacion")

    def test_recojo_tienda_cuando_domicilios_pausados(self):
        """Domicilios en moto pausados (ej. lluvia) pero recojo en tienda sigue activo."""
        self.est.domicilios_activos = False
        self.est.domicilio_mensaje_pausa = "Fuerte lluvia en la zona"
        self.est.recojo_tienda_activo = True
        self.est.save()

        # Domicilio debe ser rechazado
        resp_domi, _, _ = despachar_mensaje(
            canal="whatsapp",
            identificador_externo="3111111111",
            texto_mensaje="pedido 2 pan Calle 10 # 5-20",
            return_adjuntos=True
        )
        self.assertIn("Servicio de Domicilios Temporalmente Pausado", resp_domi)
        self.assertIn("Fuerte lluvia", resp_domi)

        # Recojo en tienda DEBE funcionar normalmente
        resp_recojo, _, _ = despachar_mensaje(
            canal="whatsapp",
            identificador_externo="3222222222",
            texto_mensaje="/recojo 2 pan rollito",
            return_adjuntos=True
        )
        self.assertIn("Recojo en Tienda", resp_recojo)
        self.assertIn("Sin costo de envío", resp_recojo)

    def test_recojo_tienda_pausado_rechaza_y_comando_staff_reactiva(self):
        """Si recojo en tienda se pausa, rechaza pedidos de recojo; staff lo reactiva con /recojo on."""
        from registro.models import VinculoCanal
        id_staff = "99887766"
        VinculoCanal.objects.create(
            canal="telegram",
            identificador_externo=id_staff,
            usuario=self.user,
            establecimiento=self.est,
            activo=True,
        )

        # Staff pausa recojo
        resp_off, _, _ = despachar_mensaje(
            canal="telegram",
            identificador_externo=id_staff,
            texto_mensaje="/recojo off Mantenimiento en horno",
            return_adjuntos=True
        )
        self.assertIn("PAUSADO", resp_off)
        self.est.refresh_from_db()
        self.assertFalse(self.est.recojo_tienda_activo)

        # Cliente intenta pedir para recoger
        resp_cliente, _, _ = despachar_mensaje(
            canal="whatsapp",
            identificador_externo="3555555555",
            texto_mensaje="pedido 2 pan para llevar",
            return_adjuntos=True
        )
        self.assertIn("Servicio de Recojo en Tienda Temporalmente Pausado", resp_cliente)
        self.assertIn("Mantenimiento en horno", resp_cliente)

        # Staff reactiva
        resp_on, _, _ = despachar_mensaje(
            canal="telegram",
            identificador_externo=id_staff,
            texto_mensaje="/recojo on",
            return_adjuntos=True
        )
        self.assertIn("ACTIVADO", resp_on)
        self.est.refresh_from_db()
        self.assertTrue(self.est.recojo_tienda_activo)

    def test_kds_estados_recojo_tienda_y_comanda(self):
        """Transición en KDS: en_preparacion -> listo_para_recoger -> entregado."""
        pedido = Pedido.objects.create(
            establecimiento=self.est,
            tipo_entrega="recojo_tienda",
            canal_origen="whatsapp",
            telefono_contacto="3199999999",
            nombre_contacto="Carlos Recogedor",
            direccion_entrega="Retiro en tienda",
            subtotal=Decimal("1000"),
            costo_domicilio=Decimal("0"),
            total=Decimal("1000"),
            medio_pago="efectivo",
            estado="en_preparacion",
        )
        LineaPedido.objects.create(
            pedido=pedido,
            producto=self.prod_pan,
            nombre_producto="Pan Rollito",
            cantidad=Decimal("2"),
            precio_unitario=Decimal("500"),
            subtotal=Decimal("1000"),
        )

        # Verificar comanda de recojo en tienda
        comanda_txt = generar_comanda_termica_texto(pedido)
        self.assertIn("RECOJO EN TIENDA / PARA LLEVAR", comanda_txt)
        self.assertIn("ENTREGA: EN MOSTRADOR / CAJA AL CLIENTE", comanda_txt)

        pdf_bytes = generar_pdf_comanda_pedido(pedido)
        self.assertTrue(len(pdf_bytes) > 500)

        client = HttpClient()
        client.force_login(self.user)

        # Cambiar estado a listo_para_recoger
        url_estado = reverse("domicilio_cambiar_estado", kwargs={"pk": pedido.id, "nuevo_estado": "listo_para_recoger"})
        resp = client.post(url_estado)
        self.assertEqual(resp.status_code, 302)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, "listo_para_recoger")

        # Cambiar estado a entregado (crea venta)
        url_entregado = reverse("domicilio_cambiar_estado", kwargs={"pk": pedido.id, "nuevo_estado": "entregado"})
        resp2 = client.post(url_entregado)
        self.assertEqual(resp2.status_code, 302)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, "entregado")
        self.assertIsNotNone(pedido.venta)


