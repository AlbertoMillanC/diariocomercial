"""
registro/test_fase1_fase2.py
Tests rigurosos para Fase 1 (Multi-Tenancy, Tokens, RBAC) y Fase 2 (bot_service, Idempotencia, Inventario Atómico).
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
    Producto,
    Venta,
    VinculoCanal,
    TokenVinculacion,
    MensajeProcesado,
)
from registro.bot_service import (
    despachar_mensaje,
    generar_token_vinculacion,
)
from registro.inventario_service import procesar_salida_inventario


class MultiTenantBotServiceTests(TestCase):
    def setUp(self):
        # Municipio piloto
        self.municipio = Municipio.objects.create(
            codigo_dane="15001",
            nombre="Tunja",
            departamento="Boyacá",
        )

        # Tienda 1: Carnicería Don Pedro
        self.tienda1 = Establecimiento.objects.create(
            nombre="Carnicería Don Pedro",
            municipio=self.municipio,
            plan_suscripcion="lanzamiento_cero",
        )
        self.user_pedro = User.objects.create_user(username="pedro", password="pass123_pedro")
        self.perfil_pedro = Perfil.objects.create(
            user=self.user_pedro, establecimiento=self.tienda1, rol="propietario"
        )
        self.act1 = ActividadCIIU.objects.create(
            establecimiento=self.tienda1, codigo="4722", descripcion="Carnes", tarifa_x_mil=Decimal("6.0")
        )
        self.prod_lomo = Producto.objects.create(
            establecimiento=self.tienda1,
            nombre="Lomo de res",
            categoria="carnes",
            precio_kilo=Decimal("30000"),
            stock_kilos=Decimal("20.000"),
        )

        # Empleado cajero en Tienda 1 (Dependiente)
        self.user_cajero = User.objects.create_user(username="cajero1", password="pass123_cajero")
        self.perfil_cajero = Perfil.objects.create(
            user=self.user_cajero, establecimiento=self.tienda1, rol="dependiente"
        )

        # Tienda 2: Minimarket Los Muiscas
        self.tienda2 = Establecimiento.objects.create(
            nombre="Minimarket Los Muiscas",
            municipio=self.municipio,
            plan_suscripcion="activo",
        )
        self.user_maria = User.objects.create_user(username="maria", password="pass123_maria")
        self.perfil_maria = Perfil.objects.create(
            user=self.user_maria, establecimiento=self.tienda2, rol="propietario"
        )
        self.act2 = ActividadCIIU.objects.create(
            establecimiento=self.tienda2, codigo="4711", descripcion="Víveres", tarifa_x_mil=Decimal("5.0")
        )
        self.prod_arroz = Producto.objects.create(
            establecimiento=self.tienda2,
            nombre="Arroz Diana 1Kg",
            categoria="abarrotes",
            precio_kilo=Decimal("4500"),
            stock_kilos=Decimal("50.000"),
        )

    def test_usuario_no_vinculado_recibe_guia(self):
        """Un remitente desconocido recibe mensaje de bienvenida y guía de vinculación."""
        resp = despachar_mensaje("telegram", "chat_desconocido_999", "Hola")
        self.assertIn("Bienvenido a DiarioComercial", resp)
        self.assertIn("Tu cuenta aún no está vinculada", resp)

    def test_vinculacion_con_token_en_un_toque(self):
        """Un usuario genera token en la web y al enviar /start auth_XYZ queda vinculado."""
        token = generar_token_vinculacion(self.user_pedro, self.tienda1)
        resp = despachar_mensaje("telegram", "chat_pedro_100", f"/start {token}")

        self.assertIn("¡Dispositivo Vinculado Exitosamente!", resp)
        self.assertIn("Carnicería Don Pedro", resp)

        # Verificar en base de datos
        vinculo = VinculoCanal.objects.filter(canal="telegram", identificador_externo="chat_pedro_100").first()
        self.assertIsNotNone(vinculo)
        self.assertEqual(vinculo.establecimiento, self.tienda1)
        self.assertEqual(vinculo.usuario, self.user_pedro)

    def test_aislamiento_multitenant_estricto(self):
        """Las ventas en Tienda 1 no afectan el stock ni la caja de Tienda 2."""
        # Vincular Pedro a Tienda 1
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="chat_pedro_1",
            usuario=self.user_pedro, establecimiento=self.tienda1
        )
        # Vincular María a Tienda 2
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="chat_maria_2",
            usuario=self.user_maria, establecimiento=self.tienda2
        )

        # Pedro vende 30 mil de lomo de res en Tienda 1
        resp_pedro = despachar_mensaje("telegram", "chat_pedro_1", "30 mil lomo de res nequi", identificador_mensaje="msg_1")
        self.assertIn("✅ *Venta Registrada:* $30.000", resp_pedro)

        # Verificar descuento en Tienda 1: 1 Kg descontado (de 20 a 19 Kg)
        self.prod_lomo.refresh_from_db()
        self.assertEqual(self.prod_lomo.stock_kilos, Decimal("19.000"))

        # Verificar que Tienda 2 permanezca intocada (50 Kg de arroz, 0 ventas)
        self.prod_arroz.refresh_from_db()
        self.assertEqual(self.prod_arroz.stock_kilos, Decimal("50.000"))
        self.assertEqual(Venta.objects.filter(establecimiento=self.tienda2).count(), 0)
        self.assertEqual(Venta.objects.filter(establecimiento=self.tienda1).count(), 1)

    def test_control_de_roles_rbac(self):
        """El dependiente puede vender pero tiene bloqueado /hoy y reportes del dueño."""
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="chat_cajero_1",
            usuario=self.user_cajero, establecimiento=self.tienda1
        )

        # El cajero registra una venta exitosamente
        resp_venta = despachar_mensaje("telegram", "chat_cajero_1", "15000 carne molida")
        self.assertIn("✅ *Venta Registrada:*", resp_venta)

        # El cajero intenta consultar el arqueo general de caja
        resp_arqueo = despachar_mensaje("telegram", "chat_cajero_1", "/hoy")
        self.assertIn("⛔ *Acceso Restringido*", resp_arqueo)
        self.assertIn("exclusivamente al propietario", resp_arqueo)

    def test_idempotencia_anti_duplicados(self):
        """Reintentos de Telegram con el mismo message_id no duplican ventas."""
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="chat_pedro_1",
            usuario=self.user_pedro, establecimiento=self.tienda1
        )

        # Enviar venta con ID de mensaje 'tg_update_99'
        resp1 = despachar_mensaje("telegram", "chat_pedro_1", "50 mil lomo", identificador_mensaje="tg_update_99")
        self.assertIn("✅ *Venta Registrada:*", resp1)
        self.assertEqual(Venta.objects.filter(establecimiento=self.tienda1).count(), 1)

        # Reintento de la red con el mismo ID
        resp2 = despachar_mensaje("telegram", "chat_pedro_1", "50 mil lomo", identificador_mensaje="tg_update_99")
        # Debe retornar la respuesta cacheada sin crear otra venta
        self.assertEqual(resp1, resp2)
        self.assertEqual(Venta.objects.filter(establecimiento=self.tienda1).count(), 1)

    def test_vinculacion_con_pin_numerico_6_digitos(self):
        """Un comerciante puede vincularse simplemente escribiendo el PIN de 6 dígitos."""
        token = generar_token_vinculacion(self.user_pedro, self.tienda1)
        pin_6 = token.replace("auth_", "")
        self.assertEqual(len(pin_6), 6)

        resp = despachar_mensaje("telegram", "chat_pedro_pin", pin_6)
        self.assertIn("¡Dispositivo Vinculado Exitosamente!", resp)
        self.assertIn("Carnicería Don Pedro", resp)

        vinculo = VinculoCanal.objects.filter(canal="telegram", identificador_externo="chat_pedro_pin").first()
        self.assertIsNotNone(vinculo)
        self.assertTrue(vinculo.activo)

    def test_kill_switch_revoca_todas_las_sesiones(self):
        """El Kill-Switch desconecta todos los dispositivos móviles inmediatamente."""
        self.client.force_login(self.user_pedro)
        # Dispositivo 1
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="chat_emp_1",
            usuario=self.user_cajero, establecimiento=self.tienda1, activo=True
        )
        # Dispositivo 2
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="chat_emp_2",
            usuario=self.user_pedro, establecimiento=self.tienda1, activo=True
        )

        self.assertEqual(VinculoCanal.objects.filter(establecimiento=self.tienda1, activo=True).count(), 2)

        # Disparar Kill-Switch por POST
        resp = self.client.post("/configuracion/", {"accion": "revocar_sesiones_moviles"})
        self.assertEqual(resp.status_code, 302)

        # Todos los vínculos quedan inactivos
        self.assertEqual(VinculoCanal.objects.filter(establecimiento=self.tienda1, activo=True).count(), 0)

        # Si el cajero despedido intenta escribir, es rechazado
        resp_bloqueado = despachar_mensaje("telegram", "chat_emp_1", "30 mil lomo")
        self.assertIn("Tu cuenta aún no está vinculada", resp_bloqueado)

    def test_landing_page_anonima_y_dashboard_autenticado(self):
        """Visitante anónimo ve Landing Page con Bre-B; usuario autenticado ve el dashboard."""
        # 1. Anónimo
        resp_anon = self.client.get("/")
        self.assertEqual(resp_anon.status_code, 200)
        self.assertContains(resp_anon, "Bre-B Banco de la República")
        self.assertContains(resp_anon, "No le regale el 3%")

        # 2. Autenticado
        self.client.force_login(self.user_pedro)
        resp_auth = self.client.get("/")
        self.assertEqual(resp_auth.status_code, 200)
        self.assertContains(resp_auth, "Carnicería Don Pedro")

