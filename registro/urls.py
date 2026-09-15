from django.urls import path
from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("ventas/nueva/", views.venta_nueva, name="venta_nueva"),
    path("compras/nueva/", views.compra_nueva, name="compra_nueva"),
    path("retenciones/nueva/", views.retencion_nueva, name="retencion_nueva"),
    path("historial/", views.historial, name="historial"),
    path("historial/<str:tipo>/<int:pk>/editar/", views.editar_movimiento, name="editar"),
    path("historial/<str:tipo>/<int:pk>/anular/", views.anular_movimiento, name="anular"),
    path("configuracion/", views.configuracion, name="configuracion"),
    path("auditoria/", views.auditoria, name="auditoria"),
    path("enviar/", views.enviar_reporte, name="enviar_reporte"),
    path("portal/", views.portal_web, name="portal_web"),
]
