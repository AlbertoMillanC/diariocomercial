"""
registro/test_fase5.py
Pruebas para Fase 5: Impresión ESC/POS, Módulo Shadow y Despacho Omnicanal con Captura CRM.
"""
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User

from registro.models import (
    Establecimiento,
    Municipio,
    Perfil,
    ActividadCIIU,
    Venta,
    Cliente,
    RegistroExogenaMunicipal,
)
from registro.print_service import generar_bytes_escpos_recibo, CMD_CUT_PAPER, CMD_INIT
from registro.shadow_service import parsear_ticket_impresion_legacy, ingestar_venta_shadow
from registro.notification_service import (
    generar_payload_whatsapp_utility,
    registrar_o_actualizar_cliente_desde_venta,
)


class Phase5ServicesTests(TestCase):
    def setUp(self):
        self.municipio = Municipio.objects.create(
            codigo_dane="15001", nombre="Tunja", departamento="Boyacá"
        )
        self.tienda = Establecimiento.objects.create(
            nombre="Minimarket La Esquina",
            nit="900987654-3",
            direccion="Calle 20 # 10-05",
            municipio=self.municipio,
        )
        self.user = User.objects.create_user(username="don_ramon", password="password123")
        self.perfil = Perfil.objects.create(
            user=self.user, establecimiento=self.tienda, rol="propietario"
        )
        self.act = ActividadCIIU.objects.create(
            establecimiento=self.tienda, codigo="4711", descripcion="Víveres", tarifa_x_mil=Decimal("5.0")
        )
        self.venta = Venta.objects.create(
            establecimiento=self.tienda,
            usuario=self.user,
            actividad=self.act,
            valor=Decimal("35000.00"),
            concepto="Arroz, Aceite y Panela",
            medio_pago="bre_b",
        )

    def test_generador_impresion_escpos(self):
        """Genera bytes crudos ESC/POS válidos con corte de papel y formato térmico."""
        raw_bytes = generar_bytes_escpos_recibo(
            venta=self.venta,
            ancho_papel_mm=58,
            url_recibo_digital="https://recibo.diariocomercial.co/r/123",
        )
        self.assertIsInstance(raw_bytes, bytes)
        self.assertTrue(raw_bytes.startswith(CMD_INIT))
        self.assertTrue(raw_bytes.endswith(CMD_CUT_PAPER))
        # Contiene datos de la tienda y de la venta
        self.assertIn(b"MINIMARKET LA ESQUINA", raw_bytes)
        self.assertIn(b"TOTAL PAGADO:", raw_bytes)
        self.assertIn(b"35.000", raw_bytes)
        self.assertIn(b"BRE-B", raw_bytes)
        self.assertIn(b"DIARIO COMERCIAL", raw_bytes)
        self.assertIn(b"3146922087", raw_bytes)

    def test_modulo_shadow_parser_e_ingesta(self):
        """El Módulo Shadow intercepta un ticket de SIIGO/POS Windows y lo procesa."""
        ticket_texto = (
            "--------------------------------\n"
            "      SUPER TIENDA BOYACA       \n"
            "       NIT: 800.555.444-1       \n"
            "--------------------------------\n"
            "1x Aceite Diana 1L        12.000\n"
            "2x Panela Pastilla         6.000\n"
            "Cliente NIT: 1049582000         \n"
            "--------------------------------\n"
            "TOTAL A PAGAR:            18.000\n"
            "--------------------------------\n"
        )

        datos = parsear_ticket_impresion_legacy(ticket_texto)
        self.assertEqual(datos["total"], Decimal("18000"))
        self.assertEqual(datos["nit_cliente"], "1049582000")
        self.assertEqual(len(datos["items"]), 2)

        # Ingestar en DiarioComercial
        exito, venta_shadow, msg = ingestar_venta_shadow(
            establecimiento=self.tienda,
            texto_o_payload_ticket=ticket_texto,
            medio_pago_detectado="efectivo",
            id_externo_ticket="SPOOL-9988",
        )
        self.assertTrue(exito)
        self.assertIsNotNone(venta_shadow)
        self.assertEqual(venta_shadow.valor, Decimal("18000"))
        self.assertEqual(venta_shadow.tipo_cliente, "empresa") # Porque tenía NIT

        # Verificar que quedó registrado automáticamente en Exógena Municipal
        exogena = RegistroExogenaMunicipal.objects.filter(
            establecimiento=self.tienda, nit_tercero="1049582000"
        ).first()
        self.assertIsNotNone(exogena)
        self.assertEqual(exogena.monto_base, Decimal("18000"))

    def test_despacho_whatsapp_utility_y_captura_crm(self):
        """Genera payload oficial Meta y registra al cliente en el CRM de barrio."""
        telefono = "3101234567"
        payload_wa = generar_payload_whatsapp_utility(
            venta=self.venta,
            telefono_cliente=telefono,
            nombre_cliente="Doña Carmen",
        )

        self.assertEqual(payload_wa["to"], "573101234567")
        self.assertEqual(payload_wa["template"]["name"], "recibo_venta_diariocomercial_v1")

        # Auto-captura de cliente en CRM
        cliente = registrar_o_actualizar_cliente_desde_venta(
            establecimiento=self.tienda,
            telefono=telefono,
            nombre="Doña Carmen",
            direccion="Carrera 5 # 12-30",
            punto_referencia="Casa verde frente a la cancha",
        )
        self.assertIsNotNone(cliente)
        self.assertEqual(cliente.telefono, "3101234567")
        self.assertEqual(cliente.nombre, "Doña Carmen")
        self.assertEqual(cliente.punto_referencia, "Casa verde frente a la cancha")
        self.assertEqual(Cliente.objects.filter(establecimiento=self.tienda).count(), 1)
