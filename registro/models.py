from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


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


class ActividadCIIU(models.Model):
    """Actividad económica del establecimiento. Un negocio puede tener varias."""

    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.CASCADE, related_name="actividades"
    )
    codigo = models.CharField(max_length=10, help_text="Ej. 4711")
    descripcion = models.CharField(max_length=160)
    tarifa_x_mil = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        help_text="Tarifa ICA, por mil. Ej. 6 = 6 x mil",
    )

    class Meta:
        unique_together = ("establecimiento", "codigo")

    def __str__(self):
        return f"{self.codigo} — {self.descripcion} ({self.tarifa_x_mil} x mil)"

    def ica_de(self, valor):
        if not valor:
            return Decimal("0")
        return (Decimal(valor) * self.tarifa_x_mil) / Decimal("1000")


class MotivoVenta(models.Model):
    """Motivos prestablecidos (ej. carnicería → Venta de carne)."""

    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.CASCADE, related_name="motivos"
    )
    actividad = models.ForeignKey(
        ActividadCIIU, on_delete=models.CASCADE, related_name="motivos"
    )
    nombre = models.CharField(max_length=120)
    es_predeterminado = models.BooleanField(default=False)

    def __str__(self):
        return self.nombre


class Venta(models.Model):
    CLIENTES = (
        ("particular", "Particular"),
        ("empresa", "Empresa"),
    )
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)
    actividad = models.ForeignKey(
        ActividadCIIU, on_delete=models.PROTECT, null=True, blank=True
    )
    motivo = models.ForeignKey(MotivoVenta, on_delete=models.PROTECT, null=True, blank=True)
    fecha = models.DateField()
    fecha_hora = models.DateTimeField(default=timezone.now)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    concepto = models.CharField(max_length=160, blank=True)
    observacion = models.CharField(max_length=160, blank=True)
    tipo_cliente = models.CharField(max_length=12, choices=CLIENTES, default="particular")
    ica_estimado = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    estado = models.CharField(max_length=12, default="vigente")

    class Meta:
        indexes = [
            models.Index(fields=["establecimiento", "fecha"], name="venta_est_fecha_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.fecha_hora:
            self.fecha = timezone.localtime(self.fecha_hora).date()
        if self.motivo and not self.concepto:
            self.concepto = self.motivo.nombre
        if self.actividad:
            self.ica_estimado = self.actividad.ica_de(self.valor)
        else:
            self.ica_estimado = Decimal("0")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Venta {self.fecha_hora} {self.valor}"


class Compra(models.Model):
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)
    fecha = models.DateField()
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    proveedor = models.CharField(max_length=120)
    concepto = models.CharField(max_length=160, blank=True)
    estado = models.CharField(max_length=12, default="vigente")

    class Meta:
        indexes = [
            models.Index(fields=["establecimiento", "fecha"], name="compra_est_fecha_idx"),
        ]

    def __str__(self):
        return f"Compra {self.proveedor} {self.valor}"


class Retencion(models.Model):
    """Solo aplica cuando la venta es a una empresa."""

    TIPOS = (
        ("fuente", "Retención en la fuente"),
        ("ica", "Retención de ICA"),
    )
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)
    venta = models.OneToOneField(
        Venta, on_delete=models.CASCADE, null=True, blank=True, related_name="retencion"
    )
    fecha = models.DateField()
    tipo = models.CharField(max_length=20, choices=TIPOS, default="ica")
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    tercero = models.CharField(max_length=120, blank=True)
    estado = models.CharField(max_length=12, default="vigente")

    class Meta:
        indexes = [
            models.Index(fields=["establecimiento", "fecha"], name="ret_est_fecha_idx"),
        ]

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
    total_retenciones = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_ica = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    def __str__(self):
        return f"Envio {self.fecha_envio} {self.estado_envio}"


class Auditoria(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True)
    entidad_afectada = models.CharField(max_length=30)
    id_registro = models.IntegerField(default=0)
    accion = models.CharField(max_length=20)
    valor_anterior = models.TextField(blank=True)
    valor_nuevo = models.TextField(blank=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=160, blank=True)

    def __str__(self):
        return f"{self.accion} {self.entidad_afectada} #{self.id_registro}"


class Producto(models.Model):
    CATEGORIAS = (
        ("carnes", "Carnes y Derivados"),
        ("viveres", "Víveres y Abarrotes"),
        ("lacteos", "Lácteos"),
        ("bebidas", "Bebidas"),
        ("otros", "Otros"),
    )
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE, related_name="productos")
    categoria = models.CharField(max_length=20, choices=CATEGORIAS, default="carnes")
    nombre = models.CharField(max_length=120)
    stock_kilos = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Existencias en Kilogramos")
    precio_kilo = models.DecimalField(max_digits=12, decimal_places=2, help_text="Precio por Kilo")
    estado = models.CharField(max_length=12, default="activo")

    class Meta:
        ordering = ["categoria", "nombre"]

    def __str__(self):
        return f"{self.nombre} - ${self.precio_kilo}/kg"

    @property
    def precio_libra(self):
        return (self.precio_kilo / Decimal("2")).quantize(Decimal("1"))

    @property
    def precio_gramo(self):
        return (self.precio_kilo / Decimal("1000")).quantize(Decimal("0.01"))

    @property
    def stock_libras(self):
        return (self.stock_kilos * Decimal("2")).quantize(Decimal("0.1"))
