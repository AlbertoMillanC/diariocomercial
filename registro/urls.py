from django.urls import path
from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("ventas/nueva/", views.venta_nueva, name="venta_nueva"),
]
