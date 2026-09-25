from django.urls import path
from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("recuperar-password/", views.recuperar_password_view, name="recuperar_password"),
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
    path("pedidos/", views.pedidos_lista, name="pedidos"),
    path("pedidos/<int:pk>/<str:accion>/", views.pedido_cambiar_estado, name="pedido_cambiar_estado"),
    path("instrucciones/", views.instrucciones, name="instrucciones"),
    # Cobro rápido Bre-B & ESC/POS
    path("api/bre-b/generar/", views.api_generar_cobro_bre_b, name="api_generar_cobro_bre_b"),
    path("api/bre-b/status/<str:referencia>/", views.api_status_bre_b, name="api_status_bre_b"),
    path("api/bre-b/mock-webhook/<str:referencia>/", views.api_mock_webhook_bre_b, name="api_mock_webhook_bre_b"),
    path("ventas/<int:pk>/ticket-escpos/<int:ancho>/", views.imprimir_ticket_escpos, name="ticket_escpos"),
    # Tributario ICA Tunja (Acuerdo 0032/2020)
    path("tributario/ica/", views.declaracion_sugerida_ica_view, name="declaracion_ica"),
    path("tributario/ica/exportar/", views.exportar_declaracion_ica, name="exportar_ica"),
    # Super-Admin Dashboard SaaS
    path("superadmin/", views.superadmin_dashboard, name="superadmin_dashboard"),
    path("superadmin/establecimiento/<int:pk>/toggle-estado/", views.superadmin_toggle_estado, name="superadmin_toggle_estado"),
]
