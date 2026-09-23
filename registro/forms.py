from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.utils import timezone

from .models import ActividadCIIU, Compra, MotivoVenta, Perfil, Producto, Retencion, Venta


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
        fields = ["fecha_hora", "actividad", "motivo", "valor", "tipo_cliente", "observacion"]
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
        self.fields["fecha_hora"].label = "Fecha y hora"
        self.fields["fecha_hora"].help_text = "Sale automática. Si necesita cambiarla, pulse el calendario."
        self.fields["observacion"].label = "Observación (opcional)"
        self.fields["observacion"].required = False
        self.fields["observacion"].widget.attrs["placeholder"] = "Ej. fiado, domicilio"

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
        fields = ["categoria", "nombre", "stock_kilos", "precio_kilo", "estado"]
        labels = {
            "categoria": "Categoría",
            "nombre": "Nombre del Producto / Corte",
            "stock_kilos": "Existencias en Kilos (Kg)",
            "precio_kilo": "Precio por Kilo ($ COP)",
            "estado": "Estado",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"placeholder": "Ej. Lomo fino de res"}),
            "stock_kilos": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
            "precio_kilo": forms.NumberInput(attrs={"step": "100", "min": "0"}),
        }
