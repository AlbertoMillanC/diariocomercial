from django.urls import path
from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("recuperar-password/", views.recuperar_password_view, name="recuperar_password"),
    path("recuperar-password/confirmar/<str:uidb64>/<str:token>/", views.recuperar_password_confirmar_view, name="recuperar_password_confirmar"),
    path("ventas/nueva/", views.venta_nueva, name="venta_nueva"),
    path("compras/nueva/", views.compra_nueva, name="compra_nueva"),
    path("retenciones/nueva/", views.retencion_nueva, name="retencion_nueva"),
    path("historial/", views.historial, name="historial"),
    path("historial/<str:tipo>/<int:pk>/editar/", views.editar_movimiento, name="editar"),
    path("historial/<str:tipo>/<int:pk>/anular/", views.anular_movimiento, name="anular"),
    path("configuracion/", views.configuracion, name="configuracion"),
    path("auditoria/", views.auditoria, name="auditoria"),
    path("enviar/", views.enviar_reporte, name="enviar_reporte"),
    path("inventario/", views.inventario_lista, name="inventario"),
    path("inventario/<int:pk>/ajustar/", views.inventario_ajustar, name="inventario_ajustar"),
    path("inventario/<int:pk>/eliminar/", views.inventario_eliminar, name="inventario_eliminar"),
    path("inventario/entrada-stock/", views.inventario_entrada_stock, name="inventario_entrada_stock"),
    path("inventario/plantilla-excel/", views.inventario_descargar_plantilla_excel, name="inventario_descargar_plantilla_excel"),
    path("inventario/importar-excel/", views.inventario_importar_excel, name="inventario_importar_excel"),
    path("inventario/<int:pk>/qr/", views.inventario_producto_qr, name="inventario_producto_qr"),
    path("inventario/<int:pk>/etiqueta-barras/", views.inventario_producto_etiqueta_barras, name="inventario_producto_etiqueta_barras"),
    path("inventario/<int:pk>/etiqueta-qr/", views.inventario_producto_etiqueta_qr, name="inventario_producto_etiqueta_qr"),
    path("inventario/etiquetas-qr/masivo/", views.inventario_etiquetas_qr_masivo, name="inventario_etiquetas_qr_masivo"),
    path("api/productos/buscar/", views.api_buscar_producto_codigo, name="api_buscar_producto_codigo"),
    path("pedidos/", views.pedidos_lista, name="pedidos"),
    path("pedidos/<int:pk>/<str:accion>/", views.pedido_cambiar_estado, name="pedido_cambiar_estado"),
    path("instrucciones/", views.instrucciones, name="instrucciones"),
    # Auditoría y Conciliación de Pagos Electrónicos
    path("pagos-electronicos/", views.pagos_electronicos_audit, name="pagos_electronicos"),
    path("pagos-electronicos/<int:pk>/conciliar/", views.pagos_electronicos_conciliar, name="pagos_electronicos_conciliar"),
    path("pagos-electronicos/exportar-excel/", views.pagos_electronicos_exportar_excel, name="pagos_electronicos_exportar_excel"),
    path("api/pagos-electronicos/<str:referencia>/comprobar/", views.api_comprobar_pago_electronico, name="api_comprobar_pago_electronico"),
    # Cobro rápido Bre-B & ESC/POS
    path("api/bre-b/generar/", views.api_generar_cobro_bre_b, name="api_generar_cobro_bre_b"),
    path("api/bre-b/status/<str:referencia>/", views.api_status_bre_b, name="api_status_bre_b"),
    path("api/bre-b/mock-webhook/<str:referencia>/", views.api_mock_webhook_bre_b, name="api_mock_webhook_bre_b"),
    path("ventas/<int:pk>/ticket-escpos/<int:ancho>/", views.imprimir_ticket_escpos, name="ticket_escpos"),
    # Administrador Multi-Tienda (Empresario & Store Switcher)
    path("mis-tiendas/", views.tiendas_lista_consolidada, name="tiendas_lista_consolidada"),
    path("mis-tiendas/nueva/", views.tiendas_crear, name="tiendas_crear"),
    path("mis-tiendas/cambiar/<int:pk>/", views.tiendas_cambiar_activa, name="tiendas_cambiar_activa"),
    # Asistente Inicial de Declaración (Onboarding Renglón por Renglón)
    path("asistente-inicial/", views.asistente_inicial_declaracion, name="asistente_inicial"),
    # Tributario ICA Tunja (Acuerdo 0032/2020)
    path("tributario/ica/", views.declaracion_sugerida_ica_view, name="declaracion_ica"),
    path("tributario/ica/exportar/", views.exportar_declaracion_ica, name="exportar_ica"),
    # Clientes & Facturación Electrónica DIAN
    path("clientes/", views.clientes_lista, name="clientes_lista"),
    path("clientes/nuevo/", views.cliente_crear, name="cliente_crear"),
    path("api/clientes/crear-rapido/", views.api_cliente_crear_rapido, name="api_cliente_crear_rapido"),
    # Información Exógena DIAN Formato 1007 (Ingresos)
    path("tributario/exogena/", views.exogena_dian_view, name="exogena_dian"),
    path("tributario/exogena/exportar-excel/", views.exogena_dian_exportar_excel, name="exogena_dian_exportar_excel"),
    # Super-Admin Dashboard SaaS, Gestión de Comercios y Cobranza
    path("superadmin/", views.superadmin_dashboard, name="superadmin_dashboard"),
    path("superadmin/comercios/nuevo/", views.superadmin_comercio_crear, name="superadmin_comercio_crear"),
    path("superadmin/establecimiento/<int:pk>/eliminar/", views.superadmin_comercio_eliminar, name="superadmin_comercio_eliminar"),
    path("superadmin/establecimiento/<int:pk>/cobrar/", views.superadmin_comercio_cobrar, name="superadmin_comercio_cobrar"),
    path("superadmin/establecimiento/<int:pk>/descargar-qr/", views.superadmin_descargar_qr_suscripcion, name="superadmin_descargar_qr_suscripcion"),
    path("superadmin/establecimiento/<int:pk>/cambiar-plan/", views.superadmin_comercio_cambiar_plan, name="superadmin_comercio_cambiar_plan"),
    path("superadmin/establecimiento/<int:pk>/toggle-estado/", views.superadmin_toggle_estado, name="superadmin_toggle_estado"),
    path("superadmin/establecimiento/<int:pk>/asistir/", views.superadmin_asistir_tienda, name="superadmin_asistir_tienda"),
    path("superadmin/cajero/<int:pk_user>/reset-password/", views.superadmin_cajero_reset_password, name="superadmin_cajero_reset_password"),
    path("superadmin/cajero/<int:pk_user>/toggle-activo/", views.superadmin_cajero_toggle_activo, name="superadmin_cajero_toggle_activo"),
    path("superadmin/cajero/<int:pk_user>/eliminar/", views.superadmin_cajero_eliminar, name="superadmin_cajero_eliminar"),
    path("superadmin/venta/<int:pk>/anular-soporte/", views.superadmin_anular_venta_soporte, name="superadmin_anular_venta_soporte"),
    # Facturación Electrónica DIAN & POS Electrónico
    path("facturacion-electronica/", views.facturacion_electronica_dashboard, name="facturacion_electronica_dashboard"),
    path("facturacion-electronica/<int:pk>/", views.factura_electronica_detalle, name="factura_electronica_detalle"),
    path("facturacion-electronica/<int:pk>/pdf/", views.factura_electronica_pdf, name="factura_electronica_pdf"),
    path("ventas/<int:pk>/emitir-factura-electronica/", views.venta_emitir_factura_electronica, name="venta_emitir_factura_electronica"),
    # Cuenta Suspendida & Reactivación
    path("cuenta-suspendida/", views.cuenta_suspendida, name="cuenta_suspendida"),
    # Configuración Central SaaS, SMTP & Segmentación por Ciudad
    path("superadmin/configuracion/", views.superadmin_configuracion, name="superadmin_configuracion"),
    path("superadmin/probar-smtp/", views.superadmin_probar_smtp, name="superadmin_probar_smtp"),
    # API Métricas en Tiempo Real para Dashboard SuperAdmin
    path("api/superadmin/metricas-tiempo-real/", views.superadmin_metricas_tiempo_real, name="superadmin_metricas_tiempo_real"),
]
