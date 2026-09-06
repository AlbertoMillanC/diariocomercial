from django import forms
from django.utils import timezone

from .models import ActividadCIIU, Compra, MotivoVenta, Retencion, Venta


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
        fields = ["fecha_hora", "actividad", "motivo", "valor", "tipo_cliente"]
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
        }


class RetencionForm(forms.ModelForm):
    class Meta:
        model = Retencion
        fields = ["fecha", "tipo", "valor", "tercero"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
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


class MotivoVentaForm(forms.ModelForm):
    class Meta:
        model = MotivoVenta
        fields = ["actividad", "nombre", "es_predeterminado"]
        labels = {
            "actividad": "Actividad CIIU",
            "nombre": "Motivo de venta",
            "es_predeterminado": "Usar este motivo por defecto",
        }
