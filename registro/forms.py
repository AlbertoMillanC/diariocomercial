from django import forms
from .models import Compra, Retencion, Venta


class VentaForm(forms.ModelForm):
    class Meta:
        model = Venta
        fields = ["fecha", "valor", "concepto", "observacion"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "concepto": forms.TextInput(attrs={"placeholder": "Ventas de mostrador"}),
            "observacion": forms.TextInput(attrs={"placeholder": "opcional"}),
        }


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
