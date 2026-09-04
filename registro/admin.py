from django.contrib import admin
from .models import (
    Establecimiento,
    Perfil,
    Venta,
    Compra,
    Retencion,
    EnvioReporte,
    Auditoria,
)


admin.site.register(Establecimiento)
admin.site.register(Perfil)
admin.site.register(Venta)
admin.site.register(Compra)
admin.site.register(Retencion)
admin.site.register(EnvioReporte)
admin.site.register(Auditoria)
