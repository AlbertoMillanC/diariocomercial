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
        """Verifica que el SuperAdmin pueda crear un nuevo comercio y su dueño con preconfiguración completa."""
        self.client.force_login(self.superadmin)
        url = reverse("superadmin_comercio_crear")
        resp = self.client.post(url, {
            "nombre": "Droguería San Jerónimo",
            "tipo_negocio": "drogueria",
            "nit": "900555666",
            "telefono_negocio": "3119998877",
            "municipio": self.municipio.pk,
            "direccion": "Av. Colón # 12-40",
            "correo_reportes": "drogueria@correo.com",
            "reportes_automaticos_activos": True,
            "frecuencia_reporte_automatico": "semanal",
            "llave_bre_b": "3201112233",
            "tipo_llave_bre_b": "celular",
            "banco_receptor_bre_b": "Daviplata",
            "plan_suscripcion": "lanzamiento_cero",
            "dias_vigencia": 30,
            "prefijo_facturacion": "FE",
            "consecutivo_inicial": 1,
            "crear_nuevo_usuario": True,
            "nombre_propietario": "Jerónimo Gómez",
            "celular_propietario": "3201112233",
            "correo_propietario": "jeronimo@drogueria.co",
            "documento_propietario": "1049582123",
            "username_propietario": "drogueria_sanjeronimo",
            "password_propietario": "clave_segura_123",
            "cargar_semilla_demo": True,
        })
        self.assertEqual(resp.status_code, 302)
        nuevo_est = Establecimiento.objects.filter(nombre="Droguería San Jerónimo").first()
        self.assertIsNotNone(nuevo_est)
        self.assertEqual(nuevo_est.tipo_negocio, "drogueria")
        self.assertEqual(nuevo_est.telefono_contacto, "3119998877")
        self.assertEqual(nuevo_est.llave_bre_b, "3201112233")
        self.assertEqual(nuevo_est.frecuencia_reporte_automatico, "semanal")
        self.assertTrue(User.objects.filter(username="drogueria_sanjeronimo").exists())
        user_creado = User.objects.get(username="drogueria_sanjeronimo")
        self.assertEqual(user_creado.perfil.telefono, "3201112233")
        self.assertEqual(user_creado.perfil.documento_identidad, "1049582123")
        # CIIU 4773 para droguería preconfigurado
        self.assertEqual(nuevo_est.actividades.first().codigo, "4773")
        # Semilla inicial demo precargada con productos
        self.assertGreaterEqual(nuevo_est.productos.count(), 3)

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

    def test_bot_venta_normal_ticket_limpio_sin_saturacion(self):
        """Verifica que una venta normal (ej: 40 mil carne nequi) no muestre saturación de factura electrónica."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje
        
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="99887766", usuario=self.dueno, establecimiento=self.est
        )
        resp_v, v, _ = despachar_mensaje("telegram", "99887766", "40 mil carne nequi", return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertEqual(v.valor, Decimal("40000"))
        self.assertIn("Ticket #", resp_v)
        # No debe saturar al comerciante con opciones de factura en ventas cotidianas
        self.assertNotIn("¿El cliente pide Factura Electrónica formal DIAN?", resp_v)
        self.assertNotIn("/factura", resp_v)

    def test_bot_flujo_conversacional_factura_en_dos_pasos(self):
        """Verifica el flujo conversacional natural: '40 mil de pan con factura' -> bot pide datos -> comerciante responde datos -> emite FE."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje

        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="77665544", usuario=self.dueno, establecimiento=self.est
        )
        # Paso 1: Venta con palabra clave factura
        resp_p1, v1, _ = despachar_mensaje("telegram", "77665544", "40 mil de pan con factura", return_adjuntos=True)
        self.assertIsNotNone(v1)
        self.assertEqual(v1.valor, Decimal("40000"))
        self.assertIn("Iniciando Factura Electrónica DIAN", resp_p1)
        self.assertIn("NIT o Cédula", resp_p1)
        self.assertIn("Nombre o Razón Social", resp_p1)
        self.assertIn("Correo electrónico", resp_p1)
        # El cliente no tiene que decir ni escribir número de ticket interno (ej: 124)
        self.assertNotIn(f"/factura {v1.pk}", resp_p1)

        # Paso 2: El comerciante responde directamente con los datos del cliente
        resp_p2 = despachar_mensaje(
            "telegram", "77665544", "1049582123 Carlos Gómez carlos@gmail.com 3101234567"
        )
        self.assertIn("¡Factura Electrónica Emitida Exitosamente!", resp_p2)
        self.assertIn("FE-00001", resp_p2)
        self.assertIn("Carlos Gómez", resp_p2)
        self.assertIn("1049582123", resp_p2)
        self.assertIn("carlos@gmail.com", resp_p2)
        self.assertIn("3101234567", resp_p2)
        self.assertIn("CUFE:", resp_p2)

        v1.refresh_from_db()
        self.assertTrue(v1.solicita_factura_electronica)
        self.assertEqual(v1.numero_factura_electronica, "FE-00001")
        self.assertEqual(v1.cliente.telefono, "3101234567")

    def test_bot_factura_un_solo_paso(self):
        """Verifica emisión directa cuando el mensaje incluye venta y datos de facturación en una sola línea."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje

        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="66554433", usuario=self.dueno, establecimiento=self.est
        )
        msg = "50 mil carne con factura 901234567 Distribuidora Boyacá SAS compras@boyaca.co 3201112233"
        resp, v, _ = despachar_mensaje("telegram", "66554433", msg, return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertIn("Venta Registrada", resp)
        self.assertIn("¡Factura Electrónica Emitida Exitosamente!", resp)
        self.assertIn("FE-00001", resp)
        self.assertIn("Distribuidora Boyacá SAS", resp)
        self.assertIn("901234567", resp)

        v.refresh_from_db()
        self.assertTrue(v.solicita_factura_electronica)
        self.assertEqual(v.cliente.nit_cedula, "901234567")
        self.assertEqual(v.cliente.correo_electronico, "compras@boyaca.co")

    def test_bot_cancelar_flujo_factura_conversacional(self):
        """Verifica que si el comerciante escribe 'cancelar', se abandona la factura y queda como ticket consumidor final."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje

        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="55443322", usuario=self.dueno, establecimiento=self.est
        )
        resp1, v, _ = despachar_mensaje("telegram", "55443322", "30 mil queso con factura", return_adjuntos=True)
        self.assertIn("Iniciando Factura Electrónica DIAN", resp1)

        resp2 = despachar_mensaje("telegram", "55443322", "cancelar")
        self.assertIn("Solicitud de factura cancelada", resp2)
        self.assertIn("Consumidor Final", resp2)

        v.refresh_from_db()
        self.assertFalse(v.solicita_factura_electronica)
        self.assertEqual(v.numero_factura_electronica, "")

    def test_bot_venta_con_documento_directo(self):
        """Verifica venta con cédula o documento directo en el ticket (ej: 40 mil carne documento 7178367)."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje

        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="44332211", usuario=self.dueno, establecimiento=self.est
        )
        resp, v, _ = despachar_mensaje("telegram", "44332211", "40 mil carne documento 7178367", return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertEqual(v.valor, Decimal("40000"))
        self.assertIn("Ticket #", resp)
        self.assertIn("7178367", resp)

        v.refresh_from_db()
        self.assertIsNotNone(v.cliente)
        self.assertEqual(v.cliente.nit_cedula, "7178367")

    def test_bot_venta_pide_documento_conversacional(self):
        """Verifica que si escribe 'con documento' sin número, el bot lo pide y no genera venta hasta recibirlo."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje

        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="33221100", usuario=self.dueno, establecimiento=self.est
        )
        # Paso 1: Pide documento (no crea venta ni descuenta inventario todavía)
        resp1, v1, _ = despachar_mensaje("telegram", "33221100", "30 mil lomo con documento", return_adjuntos=True)
        self.assertIsNone(v1)
        self.assertIn("Para generar el ticket con documento", resp1)

        # Paso 2: Envía el documento (ahora sí genera la venta y ticket)
        resp2, v2, _ = despachar_mensaje("telegram", "33221100", "7178367 Pedro Pérez", return_adjuntos=True)
        self.assertIsNotNone(v2)
        self.assertIn("Venta Registrada", resp2)
        self.assertIn("7178367", resp2)
        self.assertEqual(v2.cliente.nit_cedula, "7178367")

    def test_bot_venta_menores_consumidor_final_por_defecto(self):
        """Verifica que sin documento sale asignado a 222222222222 (Consumidor Final para ventas menores)."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje

        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="22110099", usuario=self.dueno, establecimiento=self.est
        )
        resp, v, _ = despachar_mensaje("telegram", "22110099", "25 mil queso", return_adjuntos=True)
        self.assertIn("Consumidor Final (222222222222)", resp)

        v.refresh_from_db()
        self.assertEqual(v.cliente.nit_cedula, "222222222222")
        self.assertTrue(v.cliente.es_consumidor_final)

    def test_bot_venta_con_codigo_dian_directo_13_cedula(self):
        """Verifica venta directa con código DIAN 13 (ej: 40 mil carne 13 7178367)."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje

        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="55667788", usuario=self.dueno, establecimiento=self.est
        )
        resp, v, _ = despachar_mensaje("telegram", "55667788", "40 mil carne 13 7178367", return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertEqual(v.valor, Decimal("40000"))
        self.assertIn("Ticket #", resp)
        self.assertIn("CC 7178367 (Tipo 13 DIAN)", resp)

        v.refresh_from_db()
        self.assertIsNotNone(v.cliente)
        self.assertEqual(v.cliente.nit_cedula, "7178367")
        self.assertEqual(v.cliente.tipo_documento, "13")

    def test_bot_venta_con_codigo_dian_directo_31_nit(self):
        """Verifica venta directa con código DIAN 31 (ej: 50 mil carne 31 901234567-1)."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje

        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="66778899", usuario=self.dueno, establecimiento=self.est
        )
        resp, v, _ = despachar_mensaje("telegram", "66778899", "50 mil carne 31 901234567-1 Inversiones Boyacá", return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertEqual(v.valor, Decimal("50000"))
        self.assertIn("Ticket #", resp)
        self.assertIn("NIT 901234567-1", resp)
        self.assertIn("Tipo 31 DIAN", resp)

        v.refresh_from_db()
        self.assertIsNotNone(v.cliente)
        self.assertEqual(v.cliente.nit_cedula, "901234567-1")
        self.assertEqual(v.cliente.tipo_documento, "31")

    def test_bot_venta_codigo_dian_conversacional_poner_13(self):
        """Verifica que si escribe 'poner 13' o '13' sin documento, pide la cédula y la asigna."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje

        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="77889900", usuario=self.dueno, establecimiento=self.est
        )
        # Paso 1: Venta con 'poner 13' -> NO genera ticket todavía
        resp1, v1, _ = despachar_mensaje("telegram", "77889900", "40 mil carne poner 13", return_adjuntos=True)
        self.assertIsNone(v1)
        self.assertIn("Cédula de Ciudadanía (Tipo 13 DIAN)", resp1)

        # Paso 2: Responde con el número de cédula -> AHORA SÍ genera venta y ticket
        resp2, v2, _ = despachar_mensaje("telegram", "77889900", "7178367", return_adjuntos=True)
        self.assertIsNotNone(v2)
        self.assertIn("Ticket #", resp2)
        self.assertIn("CC 7178367", resp2)
        self.assertIn("Tipo 13 DIAN", resp2)
        self.assertEqual(v2.cliente.nit_cedula, "7178367")
        self.assertEqual(v2.cliente.tipo_documento, "13")

    def test_bot_venta_con_codigo_barras_producto_y_dian(self):
        """Verifica venta por código rápido de producto (ej: 40 mil 2311412413 13 7178367)."""
        from registro.models import Producto, VinculoCanal
        from registro.bot_service import despachar_mensaje

        p_pan = Producto.objects.create(
            establecimiento=self.est,
            nombre="Pan campesino",
            codigo_barras="2311412413",
            categoria="abarrotes",
            unidad_medida="und",
            precio_kilo=Decimal("500"),
            stock_kilos=Decimal("100.0"),
        )
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="barcode_user_1", usuario=self.dueno, establecimiento=self.est
        )
        resp, v, _ = despachar_mensaje("telegram", "barcode_user_1", "40 mil 2311412413 13 7178367", return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertEqual(v.producto, p_pan)
        self.assertEqual(v.cantidad, Decimal("80.000"))
        self.assertEqual(v.unidad_medida, "und")
        self.assertEqual(v.cliente.nit_cedula, "7178367")
        self.assertEqual(v.cliente.tipo_documento, "13")
        self.assertIn("Pan campesino (80 und)", v.concepto)
        self.assertIn("CC 7178367 (Tipo 13 DIAN)", resp)

    def test_bot_venta_13_mil_no_es_codigo_dian(self):
        """Verifica que '13 mil carne' se interprete como $13.000 COP y no como código DIAN 13."""
        from registro.models import VinculoCanal
        from registro.bot_service import despachar_mensaje

        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="88990011", usuario=self.dueno, establecimiento=self.est
        )
        resp, v, _ = despachar_mensaje("telegram", "88990011", "13 mil carne", return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertEqual(v.valor, Decimal("13000"))
        self.assertIn("Consumidor Final (222222222222)", resp)
        self.assertNotIn("Tipo 13 DIAN", resp)

    def test_bot_venta_peso_gramos_en_ticket_y_factura(self):
        """Verifica que productos por peso calculen gramos exactos y se reflejen en ticket y factura."""
        from registro.models import Producto, VinculoCanal
        from registro.bot_service import despachar_mensaje
        from registro.recibo_service import generar_pdf_factura_electronica_dian, generar_pdf_recibo_venta

        Producto.objects.create(
            establecimiento=self.est,
            nombre="Carne para asar",
            categoria="carnes",
            unidad_medida="kg",
            precio_kilo=Decimal("25000"),
            stock_kilos=Decimal("20.0"),
        )
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="gramos_user_1", usuario=self.dueno, establecimiento=self.est
        )
        # $40.000 / $25 por gramo = 1.600 gramos
        resp, v, _ = despachar_mensaje("telegram", "gramos_user_1", "40 mil carne", return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertEqual(v.cantidad, Decimal("1600.000"))
        self.assertEqual(v.unidad_medida, "g")
        self.assertIn("1.600 g", v.concepto)
        self.assertIn("Kg", resp)

        # Verificar generación de PDF de factura electrónica y recibo
        pdf_fe = generar_pdf_factura_electronica_dian(v)
        self.assertGreater(len(pdf_fe), 1000)
        pdf_recibo = generar_pdf_recibo_venta(v)
        self.assertGreater(len(pdf_recibo), 500)

    def test_bot_venta_unidades_en_ticket_y_factura(self):
        """Verifica que productos por unidades se vendan y descuenten en unidades (sin hablar de gramos/kilos)."""
        from registro.models import Producto, VinculoCanal
        from registro.bot_service import despachar_mensaje
        from registro.recibo_service import generar_pdf_factura_electronica_dian

        p_pan = Producto.objects.create(
            establecimiento=self.est,
            nombre="Pan campesino",
            categoria="abarrotes",
            unidad_medida="und",
            precio_kilo=Decimal("500"),
            stock_kilos=Decimal("100.0"),
        )
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="und_user_1", usuario=self.dueno, establecimiento=self.est
        )
        # 10 mil de pan campesino a $500 c/u = 20 unidades
        resp, v, _ = despachar_mensaje("telegram", "und_user_1", "10 mil pan", return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertEqual(v.cantidad, Decimal("20.000"))
        self.assertEqual(v.unidad_medida, "und")
        self.assertIn("20 und", v.concepto)
        self.assertIn("80 und", resp)

        p_pan.refresh_from_db()
        self.assertEqual(p_pan.stock_kilos, Decimal("80.0"))

        pdf_fe = generar_pdf_factura_electronica_dian(v)
        self.assertGreater(len(pdf_fe), 1000)

    def test_bot_venta_liquidos_ml_en_ticket_y_factura(self):
        """Verifica que productos líquidos se vendan y descuenten en ml o litros."""
        from registro.models import Producto, VinculoCanal
        from registro.bot_service import despachar_mensaje
        from registro.recibo_service import generar_pdf_factura_electronica_dian

        p_aceite = Producto.objects.create(
            establecimiento=self.est,
            nombre="Aceite de girasol",
            categoria="abarrotes",
            unidad_medida="lt",
            precio_kilo=Decimal("12000"),
            stock_kilos=Decimal("10.0"),
        )
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="liq_user_1", usuario=self.dueno, establecimiento=self.est
        )
        # 500 ml a $12.000/lt = $6.000 COP
        resp, v, _ = despachar_mensaje("telegram", "liq_user_1", "500 ml aceite", return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertEqual(v.valor, Decimal("6000"))
        self.assertEqual(v.cantidad, Decimal("500.000"))
        self.assertEqual(v.unidad_medida, "ml")
        self.assertIn("500 ml", v.concepto)
        self.assertIn("9.500 Lt", resp)

        p_aceite.refresh_from_db()
        self.assertEqual(p_aceite.stock_kilos, Decimal("9.500"))

        pdf_fe = generar_pdf_factura_electronica_dian(v)
        self.assertGreater(len(pdf_fe), 1000)

    def test_bot_venta_unidades_conteo_directo(self):
        """Verifica venta directa por conteo (ej: '3 cervezas') con cálculo de valor y deducción en unidades."""
        from registro.models import Producto, VinculoCanal
        from registro.bot_service import despachar_mensaje
        from registro.recibo_service import generar_pdf_factura_electronica_dian

        p_cerveza = Producto.objects.create(
            establecimiento=self.est,
            nombre="Cerveza Aguila",
            categoria="bebidas",
            unidad_medida="und",
            precio_kilo=Decimal("3500"),
            stock_kilos=Decimal("24.0"),
        )
        VinculoCanal.objects.create(
            canal="telegram", identificador_externo="und_direct_1", usuario=self.dueno, establecimiento=self.est
        )
        resp, v, _ = despachar_mensaje("telegram", "und_direct_1", "3 cervezas", return_adjuntos=True)
        self.assertIsNotNone(v)
        self.assertEqual(v.valor, Decimal("10500"))
        self.assertEqual(v.cantidad, Decimal("3.000"))
        self.assertEqual(v.unidad_medida, "und")
        self.assertIn("3 und", v.concepto)
        self.assertIn("21 und", resp)

        p_cerveza.refresh_from_db()
        self.assertEqual(p_cerveza.stock_kilos, Decimal("21.0"))

        pdf_fe = generar_pdf_factura_electronica_dian(v)
        self.assertGreater(len(pdf_fe), 1000)



