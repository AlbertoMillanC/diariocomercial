from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Municipio(models.Model):
    """Municipio de Colombia según codificación DANE (ej. 15001 Tunja, 11001 Bogotá)."""
    codigo_dane = models.CharField(max_length=10, unique=True, db_index=True)
    nombre = models.CharField(max_length=80)
    departamento = models.CharField(max_length=80)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.departamento}) - {self.codigo_dane}"


class Establecimiento(models.Model):
    PLANES_SUSCRIPCION = (
        ("lanzamiento_cero", "Plan $0 (Lanzamiento / Prueba)"),
        ("activo", "Activo (Al Día)"),
        ("mora", "En Mora"),
        ("suspendido", "Suspendido"),
    )
    MODOS_OPERACION = (
        ("nativo", "Modo Nativo (POS Principal)"),
        ("shadow", "Modo Shadow (Intercepta POS Legacy)"),
    )
    nombre = models.CharField(max_length=120)
    nit = models.CharField(max_length=20, blank=True)
    municipio = models.ForeignKey(
        Municipio, on_delete=models.SET_NULL, null=True, blank=True, related_name="establecimientos"
    )
    direccion = models.CharField(max_length=160, blank=True)
    actividad_economica = models.CharField(max_length=80, blank=True)
    correo_reportes = models.EmailField(blank=True)
    estado = models.CharField(max_length=12, default="activo")
    
    # Parámetros Bre-B (Interoperabilidad BanRep)
    llave_bre_b = models.CharField(max_length=60, blank=True, help_text="Celular, NIT o Alias Bre-B registrado")
    tipo_llave_bre_b = models.CharField(max_length=20, default="celular")
    banco_receptor_bre_b = models.CharField(max_length=80, blank=True, help_text="Entidad financiera receptora")
    
    # Billing SaaS & Modo de Entrada
    plan_suscripcion = models.CharField(max_length=20, choices=PLANES_SUSCRIPCION, default="lanzamiento_cero")
    modo_operacion = models.CharField(max_length=20, choices=MODOS_OPERACION, default="nativo")
    fecha_fin_prueba = models.DateField(null=True, blank=True)
    fecha_ultimo_pago = models.DateField(null=True, blank=True)
    bono_incentivo_acumulado = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    # Parámetros Facturación Electrónica DIAN
    facturacion_electronica_habilitada = models.BooleanField(default=True)
    resolucion_dian_numero = models.CharField(max_length=60, blank=True, default="18764000001", help_text="Resolución DIAN de Facturación")
    prefijo_facturacion = models.CharField(max_length=10, blank=True, default="FE", help_text="Prefijo asignado por la DIAN (ej: FE)")
    rango_desde = models.PositiveIntegerField(default=1)
    rango_hasta = models.PositiveIntegerField(default=5000)
    consecutivo_actual = models.PositiveIntegerField(default=1)

    def siguiente_consecutivo_factura(self):
        consec = self.consecutivo_actual
        numero = f"{self.prefijo_facturacion}-{consec:05d}"
        self.consecutivo_actual += 1
        self.save(update_fields=["consecutivo_actual"])
        return numero

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
    MEDIOS_PAGO = (
        ("efectivo", "Efectivo"),
        ("nequi", "Nequi"),
        ("daviplata", "Daviplata"),
        ("transferencia", "Transferencia"),
        ("bre_b", "Bre-B"),
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
    medio_pago = models.CharField(max_length=20, choices=MEDIOS_PAGO, default="efectivo")
    ica_estimado = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    estado = models.CharField(max_length=12, default="vigente")
    comprobante_bancario = models.CharField(
        max_length=100, blank=True, help_text="N° de comprobante / aprobación electrónica"
    )
    conciliado_banco = models.BooleanField(
        default=False, help_text="Verificado contra extracto bancario o riel"
    )
    fecha_conciliacion = models.DateTimeField(
        null=True, blank=True, help_text="Fecha de verificación en extracto"
    )
    cliente = models.ForeignKey(
        "Cliente", on_delete=models.SET_NULL, null=True, blank=True, related_name="ventas"
    )
    solicita_factura_electronica = models.BooleanField(
        default=False, help_text="Cliente solicitó Factura Electrónica con reporte individual DIAN"
    )
    numero_factura_electronica = models.CharField(
        max_length=30, blank=True, help_text="Número consecutivo fiscal (ej: FE-00142)"
    )
    cufe = models.CharField(
        max_length=120, blank=True, help_text="Código Único de Facturación Electrónica DIAN"
    )
    estado_dian = models.CharField(
        max_length=20,
        choices=(
            ("no_requerida", "Consumidor Final (No requerida)"),
            ("pendiente", "Pendiente Envío DIAN"),
            ("aprobada", "Aprobada DIAN"),
            ("rechazada", "Rechazada DIAN"),
        ),
        default="no_requerida",
    )

    @staticmethod
    def generar_cufe(establecimiento, numero_factura, valor, fecha_hora, nit_adquirente):
        import hashlib
        cufe_raw = f"{numero_factura}{fecha_hora}{valor}{nit_adquirente}{establecimiento.nit}"
        return hashlib.sha384(cufe_raw.encode("utf-8")).hexdigest()

    class Meta:
        indexes = [
            models.Index(fields=["establecimiento", "fecha"], name="venta_est_fecha_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.fecha and self.fecha_hora:
            self.fecha = timezone.localtime(self.fecha_hora).date()
        elif self.fecha and self.fecha_hora and timezone.localtime(self.fecha_hora).date() != self.fecha:
            self.fecha_hora = self.fecha_hora.replace(year=self.fecha.year, month=self.fecha.month, day=self.fecha.day)
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
    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.SET_NULL, null=True, blank=True, related_name="auditorias"
    )
    usuario = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True)
    entidad_afectada = models.CharField(max_length=30)
    id_registro = models.IntegerField(default=0)
    accion = models.CharField(max_length=30)
    valor_anterior = models.TextField(blank=True)
    valor_nuevo = models.TextField(blank=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.accion} {self.entidad_afectada} #{self.id_registro}"


class Producto(models.Model):
    CATEGORIAS = (
        ("carnes", "Carnes y Embutidos"),
        ("abarrotes", "Víveres y Abarrotes"),
        ("lacteos", "Lácteos y Huevos"),
        ("fruver", "Frutas y Verduras"),
        ("bebidas", "Bebidas"),
        ("aseo", "Aseo y Limpieza"),
        ("servicios", "Servicios y Mano de Obra"),
        ("otros", "Otros"),
    )
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE, related_name="productos")
    categoria = models.CharField(max_length=20, choices=CATEGORIAS, default="carnes")
    nombre = models.CharField(max_length=120)
    codigo_barras = models.CharField(
        max_length=64, blank=True, db_index=True, help_text="Código de barras EAN-13, SKU o referencia"
    )
    es_servicio = models.BooleanField(
        default=False, help_text="Marcar si es un servicio o mano de obra sin control de stock físico"
    )
    unidad_medida = models.CharField(
        max_length=20, default="kg", help_text="kg, lb, und, paquete, servicio"
    )
    costo_unitario = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0"), help_text="Costo de compra al proveedor"
    )
    stock_kilos = models.DecimalField(max_digits=12, decimal_places=3, default=0, help_text="Existencias en Kilogramos o Unidades")
    precio_kilo = models.DecimalField(max_digits=12, decimal_places=2, help_text="Precio por Kilo o Unidad")
    estado = models.CharField(max_length=12, default="activo")

    class Meta:
        ordering = ["categoria", "nombre"]

    def __str__(self):
        return f"{self.nombre} - ${self.precio_kilo}/kg"

    @property
    def precio_libra(self):
        return (Decimal(str(self.precio_kilo)) / Decimal("2")).quantize(Decimal("1"))

    @property
    def precio_gramo(self):
        return (Decimal(str(self.precio_kilo)) / Decimal("1000")).quantize(Decimal("0.01"))

    @property
    def stock_libras(self):
        return (Decimal(str(self.stock_kilos)) * Decimal("2")).quantize(Decimal("0.1"))

    @property
    def stock_gramos(self):
        return int((Decimal(str(self.stock_kilos)) * Decimal("1000")).quantize(Decimal("1")))


class ItemPedido(models.Model):
    """Productos faltantes, con stock bajo o solicitados por clientes para la lista de compras/pedidos."""

    ORIGEN_CHOICES = (
        ("agotado", "Inventario Agotado / Por Reponer"),
        ("stock_bajo", "Stock Bajo"),
        ("solicitado", "Solicitado por Cliente (No existe en catálogo)"),
        ("manual", "Agregado Manualmente"),
    )
    ESTADO_CHOICES = (
        ("pendiente", "Pendiente por comprar"),
        ("comprado", "Comprado"),
        ("descartado", "Descartado"),
    )

    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.CASCADE, related_name="pedidos_compra"
    )
    producto = models.ForeignKey(
        Producto, on_delete=models.SET_NULL, null=True, blank=True, related_name="pedidos"
    )
    nombre_producto = models.CharField(max_length=120)
    categoria = models.CharField(max_length=20, choices=Producto.CATEGORIAS, default="otros")
    cantidad_sugerida = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("1"), help_text="Cantidad sugerida a comprar"
    )
    unidad = models.CharField(max_length=20, default="Kg", help_text="Kg, lb, unidades, cubeta, etc.")
    origen = models.CharField(max_length=20, choices=ORIGEN_CHOICES, default="agotado")
    veces_solicitado = models.PositiveIntegerField(
        default=1, help_text="Número de veces que fue solicitado o consultado"
    )
    observacion = models.CharField(max_length=200, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default="pendiente")

    class Meta:
        ordering = ["-fecha_actualizacion"]

    def __str__(self):
        return f"{self.nombre_producto} ({self.get_origen_display()}) - {self.estado}"


# ============================================================================
# FASE 1: MULTI-TENANCY, SEGURIDAD & VINCULACIÓN DE CANALES
# ============================================================================

class VinculoCanal(models.Model):
    """Asociación permanente entre un canal de mensajería (Telegram/WhatsApp) y un usuario/tienda."""
    CANALES = (
        ("telegram", "Telegram"),
        ("whatsapp", "WhatsApp"),
    )
    canal = models.CharField(max_length=20, choices=CANALES, default="telegram")
    identificador_externo = models.CharField(
        max_length=64, db_index=True, help_text="chat_id de Telegram o número telefónico de WhatsApp"
    )
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="vinculos_canal")
    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.CASCADE, related_name="vinculos_canal"
    )
    username_externo = models.CharField(max_length=80, blank=True)
    nombre_remitente = models.CharField(max_length=120, blank=True)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("canal", "identificador_externo")
        indexes = [
            models.Index(fields=["canal", "identificador_externo"], name="vinculo_canal_idx"),
        ]

    def __str__(self):
        return f"{self.canal}:{self.identificador_externo} ➔ {self.establecimiento.nombre} ({self.usuario.username})"


class TokenVinculacion(models.Model):
    """Token criptográfico efímero para vinculación en 1 toque (Deep Linking: t.me/bot?start=auth_XYZ)."""
    token = models.CharField(max_length=64, unique=True, db_index=True)
    usuario = models.ForeignKey(User, on_delete=models.CASCADE)
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    expira = models.DateTimeField()
    usado = models.BooleanField(default=False)

    def es_valido(self):
        return not self.usado and timezone.now() <= self.expira

    def __str__(self):
        return f"Token {self.token[:8]}... ➔ {self.establecimiento.nombre}"


class MensajeProcesado(models.Model):
    """Idempotencia: previene duplicados causados por reintentos de red de Telegram o WhatsApp."""
    canal = models.CharField(max_length=20)
    identificador_mensaje = models.CharField(max_length=128, unique=True, db_index=True)
    fecha = models.DateTimeField(auto_now_add=True)
    respuesta_cacheada = models.TextField(blank=True)

    def __str__(self):
        return f"{self.canal}:{self.identificador_mensaje}"


# ============================================================================
# FASE 2: CRM POPULAR, FIADOS & DOMICILIOS HIPERLOCALES
# ============================================================================

class Cliente(models.Model):
    """
    CRM y Directorio de Terceros / Adquirentes para Facturación Electrónica DIAN y Exógena.
    Permite tanto el registro normativo de 'CONSUMIDOR FINAL' (222222222222) como
    la identificación individual de clientes con sus requisitos tributarios.
    """
    TIPOS_DOC = (
        ("13", "Cédula de Ciudadanía (CC)"),
        ("31", "NIT (Número de Identificación Tributaria)"),
        ("22", "Cédula de Extranjería (CE)"),
        ("41", "Pasaporte"),
        ("42", "Documento de Identificación Extranjero"),
        ("47", "Permiso por Protección Temporal (PPT)"),
    )
    REGIMENES = (
        ("no_responsable_iva", "No responsable de IVA (Persona Natural)"),
        ("responsable_iva", "Responsable de IVA (Común)"),
        ("simple", "Régimen Simple de Tributación (RST)"),
        ("gran_contribuyente", "Gran Contribuyente"),
    )
    TIPOS_PERSONA = (
        ("natural", "Persona Natural"),
        ("juridica", "Persona Jurídica"),
    )

    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.CASCADE, related_name="clientes")
    nombre = models.CharField(max_length=120, help_text="Nombre completo o Razón Social del cliente")
    tipo_documento = models.CharField(max_length=5, choices=TIPOS_DOC, default="13", help_text="Tipo de documento según catálogo DIAN")
    nit_cedula = models.CharField(max_length=20, default="222222222222", db_index=True, help_text="Cédula, NIT o 222222222222 para Consumidor Final")
    dv = models.CharField(max_length=1, blank=True, help_text="Dígito de verificación (solo si es NIT)")
    tipo_persona = models.CharField(max_length=15, choices=TIPOS_PERSONA, default="natural")
    regimen_fiscal = models.CharField(max_length=30, choices=REGIMENES, default="no_responsable_iva")
    correo_electronico = models.EmailField(blank=True, help_text="Obligatorio por la DIAN para entrega de factura electrónica")
    telefono = models.CharField(max_length=20, db_index=True, blank=True, help_text="WhatsApp celular")
    direccion = models.CharField(max_length=160, blank=True, default="Tunja, Boyacá")
    municipio_nombre = models.CharField(max_length=80, blank=True, default="Tunja")
    departamento_nombre = models.CharField(max_length=80, blank=True, default="Boyacá")
    punto_referencia = models.CharField(
        max_length=160, blank=True, help_text="Crucial en barrios: 'frente a la panadería, reja negra'"
    )
    saldo_fiado = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    cupo_credito_maximo = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("100000"))
    es_consumidor_final = models.BooleanField(default=False, help_text="True si corresponde al Consumidor Final de mostrador")
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_ultimo_pedido = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["establecimiento", "telefono"], name="cliente_est_tel_idx"),
            models.Index(fields=["establecimiento", "nit_cedula"], name="cliente_est_nit_idx"),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.nit_cedula})"


# ============================================================================
# FASE 3: BRE-B (PAGOS INTEROPERABLES EN TIEMPO REAL BANREP)
# ============================================================================

class TransaccionBreB(models.Model):
    """Registro de pagos interoperables inmediatos cuenta a cuenta (Bre-B / BanRep)."""
    ESTADOS = (
        ("iniciada", "Esperando Pago"),
        ("aprobada", "Aprobada y Liquidada"),
        ("rechazada", "Rechazada por Banco"),
        ("expirada", "Expirada"),
    )
    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.CASCADE, related_name="transacciones_bre_b"
    )
    referencia_unica = models.CharField(max_length=64, unique=True, db_index=True)
    token_visual_corto = models.CharField(
        max_length=8, blank=True, help_text="Micro-token de 3 dígitos (ej: #819) para validación en mostrador"
    )
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    llave_utilizada = models.CharField(max_length=60)
    id_transaccion_banrep = models.CharField(max_length=80, blank=True, null=True, db_index=True)
    banco_origen = models.CharField(max_length=80, blank=True)
    payload_emvco = models.TextField(blank=True)
    venta = models.OneToOneField(
        Venta, on_delete=models.SET_NULL, null=True, blank=True, related_name="transaccion_bre_b"
    )
    comando_original = models.CharField(max_length=160, blank=True)
    estado = models.CharField(max_length=15, choices=ESTADOS, default="iniciada")
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_confirmacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["referencia_unica", "estado"], name="breb_ref_est_idx"),
        ]

    def __str__(self):
        return f"Bre-B {self.referencia_unica[-8:]} ${self.monto} ({self.estado})"


# ============================================================================
# FASE 4: TRIBUTARIO MULTI-MUNICIPIO & EXÓGENA MUNICIPAL
# ============================================================================

class ReglaTributariaMunicipio(models.Model):
    """Reglas paramétricas del Estatuto Tributario Municipal (ICA, Avisos, Bomberil)."""
    municipio = models.OneToOneField(
        Municipio, on_delete=models.CASCADE, related_name="regla_ica"
    )
    porcentaje_avisos_y_tableros = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("15.00"), help_text="% sobre impuesto neto ICA (Ley 97/1913)"
    )
    porcentaje_sobretasa_bomberil = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("5.00"), help_text="% sobre impuesto neto ICA (Ley 1575/2012)"
    )
    base_minima_declarante_uvt = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"), help_text="0 si declara desde el primer peso"
    )
    meses_periodo_ica = models.PositiveSmallIntegerField(
        default=2, help_text="2 para bimestral (Tunja régimen común), 12 para anual"
    )
    acuerdo_municipal_referencia = models.CharField(
        max_length=100, default="Acuerdo 0032 de 2020", help_text="Norma local vigente"
    )

    def __str__(self):
        return f"Regla ICA {self.municipio.nombre} (Avisos: {self.porcentaje_avisos_y_tableros}%, Bomberos: {self.porcentaje_sobretasa_bomberil}%)"


class RegistroExogenaMunicipal(models.Model):
    """Base de auditoría para la generación anual de Medios Magnéticos Municipales (Exógena)."""
    TIPOS = (
        ("compra", "Compras y Servicios Recibidos"),
        ("venta", "Ingresos Obtenidos por Tercero"),
        ("reteica_practicado", "Retenciones de ICA Practicadas"),
        ("reteica_asumido", "Retenciones de ICA que le practicaron"),
    )
    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.CASCADE, related_name="registros_exogena"
    )
    municipio = models.ForeignKey(Municipio, on_delete=models.CASCADE)
    año_gravable = models.PositiveIntegerField(default=2026)
    tipo_registro = models.CharField(max_length=25, choices=TIPOS)
    nit_tercero = models.CharField(max_length=20, db_index=True)
    nombre_razon_social = models.CharField(max_length=160)
    direccion_tercero = models.CharField(max_length=160, blank=True)
    monto_base = models.DecimalField(max_digits=14, decimal_places=2)
    monto_impuesto_retencion = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    tarifa_aplicada = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    fecha_transaccion = models.DateField()

    class Meta:
        indexes = [
            models.Index(fields=["establecimiento", "año_gravable", "tipo_registro"], name="exogena_est_año_idx"),
        ]

    def __str__(self):
        return f"Exógena {self.año_gravable} [{self.tipo_registro}] {self.nit_tercero}: ${self.monto_base}"

