from django.db import models
from django.contrib.auth.models import User


class Establecimiento(models.Model):
    nombre = models.CharField(max_length=120)
    nit = models.CharField(max_length=20, blank=True)
    direccion = models.CharField(max_length=160, blank=True)
    actividad_economica = models.CharField(max_length=80, blank=True)
    correo_reportes = models.EmailField(blank=True)
    estado = models.CharField(max_length=12, default="activo")

    def __str__(self):
        return self.nombre


class Perfil(models.Model):
    ROLES = (
        ("propietario", "Propietario"),
        ("dependiente", "Dependiente"),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE)
    rol = models.CharField(max_length=20, choices=ROLES, default="dependiente")

    def __str__(self):
        return f"{self.user.username} ({self.rol})"

    def es_propietario(self):
        return self.rol == "propietario"


class Venta(models.Model):
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)
    fecha = models.DateField()
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    concepto = models.CharField(max_length=160)
    observacion = models.CharField(max_length=160, blank=True)
    estado = models.CharField(max_length=12, default="vigente")

    def __str__(self):
        return f"Venta {self.fecha} {self.valor}"


class Compra(models.Model):
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)
    fecha = models.DateField()
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    proveedor = models.CharField(max_length=120)
    concepto = models.CharField(max_length=160, blank=True)
    estado = models.CharField(max_length=12, default="vigente")

    def __str__(self):
        return f"Compra {self.proveedor} {self.valor}"


class Retencion(models.Model):
    TIPOS = (
        ("fuente", "Retención en la fuente"),
        ("ica", "Retención de ICA"),
    )
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)
    fecha = models.DateField()
    tipo = models.CharField(max_length=20, choices=TIPOS, default="ica")
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    tercero = models.CharField(max_length=120, blank=True)
    estado = models.CharField(max_length=12, default="vigente")

    def __str__(self):
        return f"Retencion {self.tipo} {self.valor}"


class EnvioReporte(models.Model):
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)
    periodo_inicio = models.DateField()
    periodo_fin = models.DateField()
    correo_destino = models.EmailField()
    fecha_envio = models.DateTimeField(auto_now_add=True)
    estado_envio = models.CharField(max_length=12, default="enviado")
    total_ingresos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_egresos = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    def __str__(self):
        return f"Envio {self.fecha_envio} {self.estado_envio}"


class Auditoria(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)
    entidad_afectada = models.CharField(max_length=30)
    id_registro = models.IntegerField()
    accion = models.CharField(max_length=12)
    valor_anterior = models.TextField(blank=True)
    valor_nuevo = models.TextField(blank=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=160, blank=True)

    def __str__(self):
        return f"{self.accion} {self.entidad_afectada} #{self.id_registro}"
