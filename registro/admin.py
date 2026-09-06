from django.contrib import admin
from .models import (
    ActividadCIIU,
    Auditoria,
    Compra,
    EnvioReporte,
    Establecimiento,
    MotivoVenta,
    Perfil,
    Retencion,
    Venta,
)

admin.site.register(Establecimiento)
admin.site.register(Perfil)
admin.site.register(ActividadCIIU)
admin.site.register(MotivoVenta)
admin.site.register(Venta)
admin.site.register(Compra)
admin.site.register(Retencion)
admin.site.register(EnvioReporte)
admin.site.register(Auditoria)
