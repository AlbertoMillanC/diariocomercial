from django import forms
from .models import Venta


class VentaForm(forms.ModelForm):
    class Meta:
        model = Venta
        fields = ["fecha", "valor", "concepto", "observacion"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "concepto": forms.TextInput(attrs={"placeholder": "Ventas de mostrador"}),
            "observacion": forms.TextInput(attrs={"placeholder": "opcional"}),
        }
