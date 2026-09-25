from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.utils import timezone

from .models import (
    ActividadCIIU,
    Compra,
    ItemPedido,
    MotivoVenta,
    Perfil,
    Producto,
    Retencion,
    Venta,
)


class LoginForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "Usuario o contraseña incorrectos.",
        "inactive": "Este usuario está inactivo. Pídale al propietario que lo active.",
    }

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username and password:
            user = User.objects.filter(username=username).first()
            if user and not user.is_active and user.check_password(password):
                raise forms.ValidationError(
                    self.error_messages["inactive"],
                    code="inactive",
                )
        return super().clean()


class VentaForm(forms.ModelForm):
    nuevo_motivo = forms.CharField(
        required=False,
        label="Otro motivo (si quiere uno nuevo)",
        widget=forms.TextInput(attrs={"placeholder": "Ej. Venta de pollo"}),
    )
    tercero = forms.CharField(
        required=False,
        label="Nombre de la empresa",
        widget=forms.TextInput(attrs={"placeholder": "Solo si vende a empresa"}),
    )
    retencion_valor = forms.DecimalField(
        required=False,
        min_value=0,
        label="Valor retenido",
        help_text="Solo si la venta es a una empresa y le retuvieron.",
    )
    retencion_tipo = forms.ChoiceField(
        required=False,
        choices=Retencion.TIPOS,
        label="Tipo de retención",
    )

    class Meta:
        model = Venta
        fields = ["fecha_hora", "actividad", "motivo", "valor", "tipo_cliente", "medio_pago", "observacion"]
        widgets = {
            "fecha_hora": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "valor": forms.NumberInput(attrs={"min": "1", "step": "1"}),
        }

    def __init__(self, *args, establecimiento=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.establecimiento = establecimiento
        self.fields["fecha_hora"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"]
        if not self.instance.pk:
            self.initial.setdefault("fecha_hora", timezone.localtime().strftime("%Y-%m-%dT%H:%M"))
        elif self.instance.fecha_hora:
            self.initial["fecha_hora"] = timezone.localtime(self.instance.fecha_hora).strftime(
                "%Y-%m-%dT%H:%M"
            )
        if establecimiento:
            acts = ActividadCIIU.objects.filter(establecimiento=establecimiento)
            self.fields["actividad"].queryset = acts
            self.fields["motivo"].queryset = MotivoVenta.objects.filter(establecimiento=establecimiento)
            if acts.count() == 1 and not self.initial.get("actividad"):
                act = acts.first()
                self.initial["actividad"] = act.pk
                pred = MotivoVenta.objects.filter(actividad=act, es_predeterminado=True).first()
                if pred:
                    self.initial["motivo"] = pred.pk
        self.fields["motivo"].required = False
        self.fields["actividad"].label = "Actividad CIIU"
        self.fields["tipo_cliente"].label = "¿A quién le vende?"
        self.fields["medio_pago"].label = "Medio de pago"
        self.fields["medio_pago"].required = False
        self.fields["medio_pago"].initial = "efectivo"
        self.fields["fecha_hora"].label = "Fecha y hora"
        self.fields["fecha_hora"].help_text = "Sale automática. Si necesita cambiarla, pulse el calendario."
        self.fields["observacion"].label = "Observación (opcional)"
        self.fields["observacion"].required = False
        self.fields["observacion"].widget.attrs["placeholder"] = "Ej. fiado, domicilio"

    def clean_medio_pago(self):
        return self.cleaned_data.get("medio_pago") or "efectivo"

    def clean_fecha_hora(self):
        dt = self.cleaned_data["fecha_hora"]
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt


class CompraForm(forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["fecha", "valor", "proveedor", "concepto"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "valor": forms.NumberInput(attrs={"min": "1", "step": "1"}),
            "proveedor": forms.TextInput(attrs={"placeholder": "Nombre del proveedor"}),
            "concepto": forms.TextInput(attrs={"placeholder": "Mercancía o gasto del local"}),
        }
        labels = {
            "fecha": "Fecha",
            "valor": "Valor",
            "proveedor": "Proveedor",
            "concepto": "Concepto",
        }


class RetencionForm(forms.ModelForm):
    class Meta:
        model = Retencion
        fields = ["fecha", "tipo", "valor", "tercero"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "valor": forms.NumberInput(attrs={"min": "1", "step": "1"}),
            "tercero": forms.TextInput(attrs={"placeholder": "Empresa que retuvo"}),
        }
        labels = {
            "fecha": "Fecha",
            "tipo": "Tipo",
            "valor": "Valor retenido",
            "tercero": "Empresa",
        }


class ActividadCIIUForm(forms.ModelForm):
    class Meta:
        model = ActividadCIIU
        fields = ["codigo", "descripcion", "tarifa_x_mil"]
        widgets = {
            "codigo": forms.TextInput(attrs={"placeholder": "4711"}),
            "descripcion": forms.TextInput(attrs={"placeholder": "Comercio al por menor de alimentos"}),
            "tarifa_x_mil": forms.NumberInput(attrs={"step": "0.01", "placeholder": "6"}),
        }
        labels = {
            "codigo": "Código CIIU",
            "descripcion": "Descripción",
            "tarifa_x_mil": "Tarifa ICA (x mil)",
        }


class UsuarioNegocioForm(forms.Form):
    first_name = forms.CharField(label="Nombre", max_length=80)
    last_name = forms.CharField(label="Apellido", max_length=80, required=False)
    username = forms.CharField(label="Usuario para entrar", max_length=80)
    password = forms.CharField(label="Contraseña", widget=forms.PasswordInput)
    rol = forms.ChoiceField(label="Rol", choices=Perfil.ROLES, initial="dependiente")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Ese usuario ya existe.")
        return username


class MotivoVentaForm(forms.ModelForm):
    class Meta:
        model = MotivoVenta
        fields = ["actividad", "nombre", "es_predeterminado"]
        labels = {
            "actividad": "Actividad CIIU",
            "nombre": "Motivo de venta",
            "es_predeterminado": "Usar este motivo por defecto",
        }


class ProductoForm(forms.ModelForm):
    class Meta:
        model = Producto
        fields = [
            "codigo_barras",
            "nombre",
            "categoria",
            "es_servicio",
            "unidad_medida",
            "costo_unitario",
            "stock_kilos",
            "precio_kilo",
            "estado",
        ]
        labels = {
            "codigo_barras": "Código de Barras / SKU / EAN-13",
            "nombre": "Nombre del Producto o Servicio",
            "categoria": "Categoría",
            "es_servicio": "¿Es un servicio / mano de obra? (Sin stock físico)",
            "unidad_medida": "Unidad de Medida",
            "costo_unitario": "Costo de Compra Proveedor ($ COP)",
            "stock_kilos": "Existencias Iniciales (Kg o Und)",
            "precio_kilo": "Precio de Venta al Público ($ COP)",
            "estado": "Estado",
        }
        widgets = {
            "codigo_barras": forms.TextInput(attrs={"placeholder": "Ej. 7701234567890 (o escanear con pistola USB)"}),
            "nombre": forms.TextInput(attrs={"placeholder": "Ej. Lomo fino de res, Arroz Diana 1kg, Servicio de Afilado"}),
            "stock_kilos": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
            "precio_kilo": forms.NumberInput(attrs={"step": "100", "min": "0"}),
            "costo_unitario": forms.NumberInput(attrs={"step": "100", "min": "0"}),
            "unidad_medida": forms.TextInput(attrs={"placeholder": "kg, lb, und, servicio"}),
        }


class EntradaStockForm(forms.Form):
    """Formulario seguro para que dependientes y cajeros SOLO puedan agregar existencias (+)."""
    producto_id = forms.IntegerField(widget=forms.HiddenInput())
    cantidad = forms.DecimalField(
        min_value=Decimal("0.05"),
        max_digits=10,
        decimal_places=3,
        label="Cantidad a Ingresar / Surtir (+)",
        help_text="Solo se permiten valores positivos para sumar al inventario.",
        widget=forms.NumberInput(attrs={"step": "0.1", "placeholder": "Ej. 10.5"}),
    )
    nota_remision = forms.CharField(
        required=False,
        max_length=120,
        label="N° Remisión o Nota Proveedor",
        widget=forms.TextInput(attrs={"placeholder": "Ej. Factura 4529 Distribuidora"}),
    )


class ImportarExcelPedidoForm(forms.Form):
    """Formulario para cargar pedidos e inventario masivo mediante archivo Excel (.xlsx)."""
    archivo_excel = forms.FileField(
        label="Seleccionar Archivo de Pedido Excel (.xlsx)",
        help_text="Suba la plantilla estándar con los productos recibidos.",
    )
    registrar_compra = forms.BooleanField(
        required=False,
        initial=True,
        label="¿Registrar como Compra / Egreso del Día?",
        help_text="Si está marcado y el archivo incluye costos, crea la compra automáticamente.",
    )
    proveedor = forms.CharField(
        required=False,
        max_length=120,
        initial="Proveedor de Pedido",
        label="Nombre del Proveedor (opcional)",
        widget=forms.TextInput(attrs={"placeholder": "Ej. Frigorífico Guadalupe, Corabastos"}),
    )

    def clean_archivo_excel(self):
        f = self.cleaned_data["archivo_excel"]
        if not f.name.endswith((".xlsx", ".xlsm")):
            raise forms.ValidationError("El archivo debe tener formato Excel (.xlsx).")
        return f


class ConciliarPagoForm(forms.Form):
    """Formulario para conciliar transferencias Nequi/Daviplata/Bancos contra extracto."""
    comprobante_bancario = forms.CharField(
        max_length=100,
        label="N° Comprobante / Aprobación Bancaria",
        widget=forms.TextInput(attrs={"placeholder": "Ej. Aprobación M128938 o Extracto Mes"}),
    )


class ItemPedidoForm(forms.ModelForm):
    class Meta:
        model = ItemPedido
        fields = ["nombre_producto", "categoria", "cantidad_sugerida", "unidad", "observacion"]
        labels = {
            "nombre_producto": "Producto a pedir",
            "categoria": "Departamento / Categoría",
            "cantidad_sugerida": "Cantidad sugerida",
            "unidad": "Unidad (Kg, lb, un, cubeta)",
            "observacion": "Nota / Proveedor sugerido (opcional)",
        }
        widgets = {
            "nombre_producto": forms.TextInput(attrs={"placeholder": "Ej. Salchichas manguera, Queso costeño"}),
            "cantidad_sugerida": forms.NumberInput(attrs={"step": "0.5", "min": "0.5"}),
            "unidad": forms.TextInput(attrs={"placeholder": "Kg, lb, bolsas, paquetes"}),
            "observacion": forms.TextInput(attrs={"placeholder": "Ej. Pedir a Distribuidora Los Andes"}),
        }
