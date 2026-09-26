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
    Establecimiento,
    Cliente,
    Municipio,
    PagoSuscripcion,
    ConfiguracionPlataformaSaaS,
    ConfiguracionSaaSMunicipio,
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
        fields = [
            "fecha_hora",
            "actividad",
            "motivo",
            "valor",
            "tipo_cliente",
            "medio_pago",
            "solicita_factura_electronica",
            "cliente",
            "observacion",
        ]
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
            self.fields["cliente"].queryset = Cliente.objects.filter(establecimiento=establecimiento).order_by("nombre")
            if acts.count() == 1 and not self.initial.get("actividad"):
                act = acts.first()
                self.initial["actividad"] = act.pk
                pred = MotivoVenta.objects.filter(actividad=act, es_predeterminado=True).first()
                if pred:
                    self.initial["motivo"] = pred.pk
        else:
            self.fields["cliente"].queryset = Cliente.objects.none()

        self.fields["cliente"].required = False
        self.fields["cliente"].label = "Cliente / Adquirente (DIAN)"
        self.fields["solicita_factura_electronica"].label = "Solicita Factura Electrónica individual DIAN"
        self.fields["solicita_factura_electronica"].required = False
        self.fields["motivo"].required = False
        self.fields["actividad"].label = "Actividad CIIU"
        self.fields["tipo_cliente"].label = "¿A quién le vende?"
        self.fields["medio_pago"].label = "Medio de pago"
        self.fields["medio_pago"].required = False
        self.fields["medio_pago"].initial = "efectivo"
        self.fields["observacion"].label = "Observación (opcional)"
        self.fields["observacion"].required = False
        self.fields["observacion"].widget.attrs["placeholder"] = "Ej. fiado, domicilio, nota"

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


class SoporteCrearCajeroForm(forms.Form):
    """Formulario para que el SuperAdmin asista creando o restaurando un cajero/dependiente."""
    first_name = forms.CharField(label="Nombre del Empleado", max_length=80)
    last_name = forms.CharField(label="Apellido", max_length=80, required=False)
    username = forms.CharField(label="Usuario de Entrada", max_length=80)
    password = forms.CharField(label="Contraseña Temporal", widget=forms.PasswordInput)
    rol = forms.ChoiceField(label="Rol en el Comercio", choices=Perfil.ROLES, initial="dependiente")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Este nombre de usuario ya existe en el sistema.")
        return username


class SoporteEditarTiendaForm(forms.ModelForm):
    """Formulario para que el SuperAdmin modifique datos de un comercio a solicitud del dueño."""
    class Meta:
        model = Establecimiento
        fields = [
            "nombre",
            "nit",
            "municipio",
            "direccion",
            "llave_bre_b",
            "tipo_llave_bre_b",
            "banco_receptor_bre_b",
            "plan_suscripcion",
            "correo_reportes",
        ]
        labels = {
            "nombre": "Nombre del Comercio",
            "nit": "NIT o Cédula",
            "municipio": "Municipio (DANE)",
            "direccion": "Dirección Física",
            "llave_bre_b": "Llave Bre-B (Celular / NIT / Alias)",
            "tipo_llave_bre_b": "Tipo de Llave Bre-B",
            "banco_receptor_bre_b": "Banco Receptor Bre-B",
            "plan_suscripcion": "Plan de Suscripción",
            "correo_reportes": "Correo del Contador Aliado",
        }


class ClienteFacturacionForm(forms.ModelForm):
    """
    Formulario de registro y edición de clientes / adquirentes para Facturación Electrónica DIAN.
    Valida requisitos normativos: Tipo de Documento, Cédula/NIT, Razón Social, Correo electrónico y Dirección.
    """
    class Meta:
        model = Cliente
        fields = [
            "tipo_documento",
            "nit_cedula",
            "dv",
            "nombre",
            "tipo_persona",
            "regimen_fiscal",
            "correo_electronico",
            "telefono",
            "direccion",
            "municipio_nombre",
            "departamento_nombre",
        ]
        labels = {
            "tipo_documento": "Tipo de Documento",
            "nit_cedula": "Número de Identificación (Cédula o NIT)",
            "dv": "DV",
            "nombre": "Nombre Completo o Razón Social",
            "tipo_persona": "Naturaleza Jurídica",
            "regimen_fiscal": "Régimen Fiscal (Responsabilidad Tributaria)",
            "correo_electronico": "Correo Electrónico (Recepción de Factura)",
            "telefono": "Teléfono / Celular WhatsApp",
            "direccion": "Dirección Fiscal",
            "municipio_nombre": "Ciudad / Municipio",
            "departamento_nombre": "Departamento",
        }
        widgets = {
            "tipo_documento": forms.Select(attrs={"class": "form-control"}),
            "nit_cedula": forms.TextInput(attrs={"placeholder": "Ej: 1049654321 o 901234567"}),
            "dv": forms.TextInput(attrs={"placeholder": "0", "style": "width: 50px;"}),
            "nombre": forms.TextInput(attrs={"placeholder": "Ej: Distribuidora Central S.A.S. o Juan Pérez"}),
            "correo_electronico": forms.EmailInput(attrs={"placeholder": "facturas@cliente.com"}),
            "telefono": forms.TextInput(attrs={"placeholder": "3101234567"}),
            "direccion": forms.TextInput(attrs={"placeholder": "Carrera 10 # 18-35"}),
            "municipio_nombre": forms.TextInput(attrs={"placeholder": "Tunja"}),
            "departamento_nombre": forms.TextInput(attrs={"placeholder": "Boyacá"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ["dv", "tipo_persona", "regimen_fiscal", "telefono", "direccion", "municipio_nombre", "departamento_nombre"]:
            if f in self.fields:
                self.fields[f].required = False

    def clean_nit_cedula(self):
        doc = self.cleaned_data.get("nit_cedula", "").strip()
        if not doc:
            raise forms.ValidationError("El número de documento es obligatorio.")
        return doc

    def clean(self):
        cleaned_data = super().clean()
        td = cleaned_data.get("tipo_documento")
        nit = cleaned_data.get("nit_cedula", "")
        dv = cleaned_data.get("dv")

        if td == "31" and not dv and nit.isdigit():
            # Algoritmo DIAN módulo 11 para calcular dígito de verificación si no fue digitado
            primos = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]
            suma = sum(int(d) * p for d, p in zip(reversed(nit), primos))
            residuo = suma % 11
            cleaned_data["dv"] = str(residuo if residuo <= 1 else 11 - residuo)

        return cleaned_data


class AsistenteDeclaracionInicialForm(forms.Form):
    """
    Formulario de configuración inicial (Onboarding Wizard) renglón por renglón
    del Formulario Oficial de Declaración de Impuestos (Formulario 02 de ICA / DIAN).
    Incluye placeholders explicativos y datos pedagógicos para el comerciante.
    """
    # SECCIÓN B: IDENTIFICACIÓN Y DATOS DEL CONTRIBUYENTE (Renglones 1 al 7)
    nit = forms.CharField(
        label="NIT o Cédula de Ciudadanía (Renglones 1 y 2)",
        max_length=20,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej: 1049654321 o 901234567 (Solo números, sin puntos ni comas)",
            "class": "form-control",
            "autofocus": "autofocus",
        }),
        help_text="Casilla 5 de su RUT o número de cédula física. Digite solo números."
    )
    nombre = forms.CharField(
        label="Razón Social o Nombre Completo (Renglón 3)",
        max_length=120,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej: Carnicería y Fruver La 20 o Distribuidora El Ganadero",
            "class": "form-control",
        }),
        help_text="Nombre de su negocio o su nombre si opera como persona natural."
    )
    direccion = forms.CharField(
        label="Dirección del Establecimiento (Renglón 4)",
        max_length=160,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej: Carrera 10 # 18-35 Local 1 Barrio Centro",
            "class": "form-control",
        }),
        help_text="Dirección física exacta donde atiende sus clientes en el municipio."
    )
    municipio = forms.ModelChoiceField(
        label="Municipio y Departamento (Renglón 5)",
        queryset=Municipio.objects.all(),
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="Municipio donde está matriculado su negocio (ej: Tunja 15001)."
    )
    telefono = forms.CharField(
        label="Teléfono / Celular WhatsApp (Renglón 6)",
        max_length=20,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej: 3101234567 (Para contacto y cobros Bre-B)",
            "class": "form-control",
        }),
        help_text="Número móvil de su negocio. Servirá además para vincular pagos Bre-B."
    )
    correo_reportes = forms.EmailField(
        label="Correo Electrónico para Reportes y Declaración (Renglón 7)",
        widget=forms.EmailInput(attrs={
            "placeholder": "Ej: miempresa@gmail.com o contador@asesoria.com",
            "class": "form-control",
        }),
        help_text="Correo donde el sistema enviará los borradores en PDF y Excel para su contador."
    )
    clasificacion_tributaria = forms.ChoiceField(
        label="Régimen y Clasificación del Negocio",
        choices=(
            ("comun", "Régimen Común / Ordinario (Declaración Bimestral en Tunja)"),
            ("simplificado", "Régimen Simplificado (Declaración Anual)"),
            ("simple_rst", "Régimen Simple de Tributación (RST)"),
        ),
        initial="comun",
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="La mayoría de minimarkets y carnicerías declaran bimestral en Régimen Común."
    )
    declara_renta_dian = forms.BooleanField(
        label="¿El comerciante o empresa declara Impuesto de Renta ante la DIAN?",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text="Marque esta casilla si presenta declaración anual de Renta (Formulario 110 o 210) ante la DIAN."
    )
    otros_ingresos_nacionales_anual = forms.DecimalField(
        label="Renglón 9: Otros ingresos brutos obtenidos fuera de este municipio ($ COP)",
        min_value=Decimal("0"),
        max_digits=14,
        decimal_places=2,
        initial=Decimal("0"),
        required=False,
        widget=forms.NumberInput(attrs={
            "placeholder": "Ej: 0 (o ventas brutas de sedes en otras ciudades, arriendos, etc.)",
            "step": "1000",
            "class": "form-control",
        }),
        help_text="Si declara renta a la DIAN, estos ingresos se consolidan en el Renglón 8 y se descuentan en el Renglón 9 para que solo tribute en Tunja."
    )

    # SECCIÓN C: ACTIVIDAD ECONÓMICA Y TARIFAS (Renglones 16 y 17)
    ciiu_codigo = forms.CharField(
        label="Código de Actividad CIIU Principal (4 dígitos)",
        max_length=10,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej: 4722 (O presione uno de los botones rápidos de abajo)",
            "class": "form-control",
        }),
        help_text="Código de 4 dígitos de la casilla 46 de su RUT (ej: 4722 Carnicería, 4711 Minimarket)."
    )
    ciiu_descripcion = forms.CharField(
        label="Descripción de la Actividad Económica",
        max_length=160,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej: Comercio al por menor de carnes y productos cárnicos en establecimientos especializados",
            "class": "form-control",
        }),
        help_text="Nombre oficial de la actividad económica inscrita."
    )
    ciiu_tarifa_x_mil = forms.DecimalField(
        label="Tarifa ICA por Mil (x mil)",
        min_value=Decimal("1.0"),
        max_digits=5,
        decimal_places=2,
        initial=Decimal("5.0"),
        widget=forms.NumberInput(attrs={
            "placeholder": "Ej: 5.0 (Tarifa legal en Tunja para comercio al por menor: 5 por mil)",
            "step": "0.1",
            "class": "form-control",
        }),
        help_text="Tarifa fijada por el Estatuto Tributario Municipal. En Tunja el comercio paga el 5.0 por mil."
    )
    motivo_nombre = forms.CharField(
        label="Concepto de Venta Principal en Mostrador",
        max_length=100,
        initial="Venta en Mostrador",
        widget=forms.TextInput(attrs={
            "placeholder": "Ej: Venta Carne de Res y Cerdo o Viveres Generales",
            "class": "form-control",
        }),
        help_text="El producto o categoría que más se vende en el mostrador para registrar ventas rápidas."
    )

    # SECCIÓN D: ANTECEDENTES Y SALDOS DE LA DECLARACIÓN ANTERIOR (Renglones 29 y 32)
    anticipo_ano_anterior = forms.DecimalField(
        label="Renglón 29: Menos Anticipo liquidado en el año anterior ($ COP)",
        min_value=Decimal("0"),
        max_digits=14,
        decimal_places=2,
        initial=Decimal("0"),
        required=False,
        widget=forms.NumberInput(attrs={
            "placeholder": "Ej: 0 (o el valor del Renglón 30 de su formulario del año pasado)",
            "step": "1000",
            "class": "form-control",
        }),
        help_text="Si en su formulario del año pasado pagó anticipo, colóquelo aquí para descontarlo este año. Si no, deje 0."
    )
    saldo_favor_anterior = forms.DecimalField(
        label="Renglón 32: Menos Saldo a favor del periodo anterior ($ COP)",
        min_value=Decimal("0"),
        max_digits=14,
        decimal_places=2,
        initial=Decimal("0"),
        required=False,
        widget=forms.NumberInput(attrs={
            "placeholder": "Ej: 0 (o el valor del Renglón 34 de su formulario del año pasado)",
            "step": "1000",
            "class": "form-control",
        }),
        help_text="Si el año pasado le quedó saldo a favor, digítelo aquí para que el sistema lo reste de su pago. Si no, deje 0."
    )

    # SECCIÓN E: FACTURACIÓN ELECTRÓNICA DIAN (OPCIONAL / DEFAULT LISTO)
    resolucion_dian = forms.CharField(
        label="Resolución DIAN de Facturación",
        max_length=60,
        initial="18764000001",
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej: 18764000001 (Número de habilitación DIAN)",
            "class": "form-control",
        }),
        help_text="Número oficial de la resolución expedida por la DIAN para facturar."
    )
    prefijo_facturacion = forms.CharField(
        label="Prefijo de Facturación",
        max_length=10,
        initial="FE",
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej: FE o SETT",
            "class": "form-control",
        }),
        help_text="Letras que anteceden al número de factura (ej: FE)."
    )
    consecutivo_inicial = forms.IntegerField(
        label="Consecutivo Inicial de Factura",
        min_value=1,
        initial=1,
        required=False,
        widget=forms.NumberInput(attrs={
            "placeholder": "Ej: 1 (O el número de su próxima factura)",
            "class": "form-control",
        }),
        help_text="Número desde el cual se empezarán a numerar las facturas electrónicas."
    )

    def clean_nit(self):
        nit = self.cleaned_data.get("nit", "").strip()
        nit_limpio = "".join(c for c in nit if c.isdigit())
        if not nit_limpio:
            raise forms.ValidationError("El NIT o Cédula debe contener números válidos.")
        return nit_limpio


class NuevaTiendaSedeForm(forms.ModelForm):
    """Formulario para que un empresario registre una nueva tienda o sucursal propia."""
    class Meta:
        model = Establecimiento
        fields = [
            "nombre",
            "nit",
            "municipio",
            "direccion",
            "llave_bre_b",
            "correo_reportes",
            "clasificacion_tributaria",
        ]
        labels = {
            "nombre": "Nombre de la Nueva Sede o Sucursal",
            "nit": "NIT o Cédula (Mismo del contribuyente o específico)",
            "municipio": "Municipio (DANE)",
            "direccion": "Dirección Física de la Sucursal",
            "llave_bre_b": "Celular / Llave Bre-B para pagos de esta sede",
            "correo_reportes": "Correo para reportes de esta sede",
            "clasificacion_tributaria": "Régimen Tributario",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"placeholder": "Ej: Carnicería La Floresta - Sede Norte", "class": "form-control"}),
            "nit": forms.TextInput(attrs={"placeholder": "Ej: 901234567", "class": "form-control"}),
            "municipio": forms.Select(attrs={"class": "form-control"}),
            "direccion": forms.TextInput(attrs={"placeholder": "Ej: Av. Universitaria # 45-12 Local 2", "class": "form-control"}),
            "llave_bre_b": forms.TextInput(attrs={"placeholder": "3109876543", "class": "form-control"}),
            "correo_reportes": forms.EmailInput(attrs={"placeholder": "sedenorte@comercio.co", "class": "form-control"}),
            "clasificacion_tributaria": forms.Select(attrs={"class": "form-control"}),
        }


class SuperadminNuevoComercioForm(forms.Form):
    """Formulario para que el Super-Administrador cree nuevos comercios (Tenants) en la plataforma."""
    # Datos de la Tienda / Comercio
    nombre = forms.CharField(
        max_length=120,
        label="Nombre Comercial del Negocio",
        widget=forms.TextInput(attrs={"placeholder": "Ej: Carnicería El Samán, Droguería Central", "class": "form-control"}),
    )
    nit = forms.CharField(
        max_length=20,
        label="NIT o Cédula del Comercio",
        widget=forms.TextInput(attrs={"placeholder": "Ej: 901456789-1 o 1049582123", "class": "form-control"}),
    )
    municipio = forms.ModelChoiceField(
        queryset=Municipio.objects.all(),
        label="Municipio (DANE)",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    direccion = forms.CharField(
        max_length=160,
        label="Dirección Física",
        widget=forms.TextInput(attrs={"placeholder": "Ej: Carrera 10 # 18-42 Centro, Tunja", "class": "form-control"}),
    )
    correo_reportes = forms.EmailField(
        required=False,
        label="Correo del Contador o Reportes",
        widget=forms.EmailInput(attrs={"placeholder": "contador@estudio.com", "class": "form-control"}),
    )
    # Medios de Pago Electrónicos del Comercio
    llave_bre_b = forms.CharField(
        required=False,
        max_length=60,
        label="Celular / Llave Bre-B del Comercio (Para recibir pagos)",
        help_text="Número de celular, NIT o alias registrado en Bre-B / BanRep",
        widget=forms.TextInput(attrs={"placeholder": "Ej: 3105554321", "class": "form-control"}),
    )
    banco_receptor_bre_b = forms.CharField(
        required=False,
        max_length=80,
        label="Banco Receptor del Comercio",
        help_text="Bancolombia, Nequi, Daviplata, Davivienda, etc.",
        widget=forms.TextInput(attrs={"placeholder": "Ej: Bancolombia / Nequi", "class": "form-control"}),
    )
    # Plan SaaS y Licenciamiento
    plan_suscripcion = forms.ChoiceField(
        choices=Establecimiento.PLANES_SUSCRIPCION,
        initial="lanzamiento_cero",
        label="Plan de Suscripción Inicial",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    dias_vigencia = forms.IntegerField(
        initial=30,
        label="Días de Vigencia Inicial",
        help_text="30 días de prueba gratuita para Plan $0, o los días contratados.",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    # Facturación Electrónica DIAN Inicial
    prefijo_facturacion = forms.CharField(
        initial="FE",
        max_length=10,
        label="Prefijo Facturación Electrónica DIAN",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    consecutivo_inicial = forms.IntegerField(
        initial=1,
        label="Consecutivo Inicial DIAN",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    # Propietario / Administrador
    crear_nuevo_usuario = forms.BooleanField(
        required=False,
        initial=True,
        label="Crear un nuevo usuario Propietario para esta tienda",
    )
    username_propietario = forms.CharField(
        required=False,
        max_length=150,
        label="Usuario del Propietario",
        widget=forms.TextInput(attrs={"placeholder": "Ej: don_carlos_saman", "class": "form-control"}),
    )
    password_propietario = forms.CharField(
        required=False,
        label="Contraseña Temporal",
        widget=forms.PasswordInput(attrs={"placeholder": "Mínimo 6 caracteres", "class": "form-control"}),
    )
    usuario_existente = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=False,
        label="O asociar a Propietario Existente (Empresario)",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        crear_nuevo = cleaned_data.get("crear_nuevo_usuario")
        if crear_nuevo:
            user = cleaned_data.get("username_propietario")
            pwd = cleaned_data.get("password_propietario")
            if not user or not user.strip():
                self.add_error("username_propietario", "Debe ingresar el nombre de usuario del propietario.")
            elif User.objects.filter(username=user.strip()).exists():
                self.add_error("username_propietario", "Ese nombre de usuario ya está en uso.")
            if not pwd or len(pwd.strip()) < 4:
                self.add_error("password_propietario", "La contraseña debe tener al menos 4 caracteres.")
        else:
            if not cleaned_data.get("usuario_existente"):
                self.add_error("usuario_existente", "Debe seleccionar un usuario existente si no va a crear uno nuevo.")
        return cleaned_data


class RegistrarPagoSuscripcionForm(forms.Form):
    """Formulario para asentar el cobro y pago de suscripción SaaS de un comercio."""
    monto = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        initial=Decimal("19900"),
        label="Valor Cobrado ($ COP)",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "100"}),
    )
    metodo = forms.ChoiceField(
        choices=PagoSuscripcion.METODOS,
        initial="bre_b",
        label="Medio de Pago Utilizado",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    periodo_dias = forms.ChoiceField(
        choices=(
            (30, "1 Mes (30 días) - $19.900"),
            (90, "Trimestre (90 días) - $55.000"),
            (180, "Semestre (180 días) - $105.000"),
            (365, "1 Año Completo (365 días) - $199.000"),
        ),
        initial=30,
        label="Periodo de Renovación",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    referencia = forms.CharField(
        required=False,
        max_length=80,
        label="N° Comprobante / Referencia de Pago",
        widget=forms.TextInput(attrs={"placeholder": "Ej: BREB-948271 o Transf. Bancolombia #129", "class": "form-control"}),
    )
    notas = forms.CharField(
        required=False,
        max_length=200,
        label="Notas u Observaciones del Cobro",
        widget=forms.TextInput(attrs={"placeholder": "Ej: Renovación oportuna mes de Octubre", "class": "form-control"}),
    )


class ConfiguracionPlataformaSaaSForm(forms.ModelForm):
    """Formulario para actualizar datos centrales de la plataforma SaaS (Bre-B, WhatsApp, SMTP)."""
    class Meta:
        model = ConfiguracionPlataformaSaaS
        fields = [
            "llave_bre_b_general",
            "tipo_llave_bre_b",
            "banco_receptor",
            "whatsapp_soporte_general",
            "tarifa_mensual_cop",
            "correo_soporte",
            "dias_gracia_mora",
            "smtp_activo",
            "smtp_host",
            "smtp_port",
            "smtp_user",
            "smtp_password",
            "smtp_use_tls",
            "smtp_from_email",
        ]
        widgets = {
            "llave_bre_b_general": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej: 3028530041"}),
            "tipo_llave_bre_b": forms.Select(
                choices=(("celular", "Número de Celular"), ("nit", "NIT / Cédula"), ("alias", "Alias / Nombre Bre-B")),
                attrs={"class": "form-control"}
            ),
            "banco_receptor": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej: Bancolombia / Nequi / Davivienda"}),
            "whatsapp_soporte_general": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej: 3146922087"}),
            "tarifa_mensual_cop": forms.NumberInput(attrs={"class": "form-control", "step": "100"}),
            "correo_soporte": forms.EmailInput(attrs={"class": "form-control", "placeholder": "soporte@diariocomercial.co"}),
            "dias_gracia_mora": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
            "smtp_activo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "smtp_host": forms.TextInput(attrs={"class": "form-control", "placeholder": "smtp-relay.brevo.com o smtp.gmail.com"}),
            "smtp_port": forms.NumberInput(attrs={"class": "form-control"}),
            "smtp_user": forms.TextInput(attrs={"class": "form-control", "placeholder": "tu-correo@dominio.com o API key"}),
            "smtp_password": forms.PasswordInput(render_value=True, attrs={"class": "form-control", "placeholder": "Contraseña SMTP o Master Key"}),
            "smtp_use_tls": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "smtp_from_email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "notificaciones@diariocomercial.co"}),
        }


class ConfiguracionSaaSMunicipioForm(forms.ModelForm):
    """Formulario para segmentar llaves de cobro y teléfonos de soporte por ciudad."""
    class Meta:
        model = ConfiguracionSaaSMunicipio
        fields = [
            "municipio",
            "llave_bre_b",
            "tipo_llave_bre_b",
            "banco_receptor",
            "whatsapp_soporte",
            "tarifa_mensual_cop",
            "activo",
        ]
        widgets = {
            "municipio": forms.Select(attrs={"class": "form-control"}),
            "llave_bre_b": forms.TextInput(attrs={"class": "form-control", "placeholder": "Dejar en blanco para usar la general"}),
            "tipo_llave_bre_b": forms.Select(
                choices=(("celular", "Número de Celular"), ("nit", "NIT / Cédula"), ("alias", "Alias / Nombre Bre-B")),
                attrs={"class": "form-control"}
            ),
            "banco_receptor": forms.TextInput(attrs={"class": "form-control", "placeholder": "Banco receptor local"}),
            "whatsapp_soporte": forms.TextInput(attrs={"class": "form-control", "placeholder": "Dejar en blanco para usar el general"}),
            "tarifa_mensual_cop": forms.NumberInput(attrs={"class": "form-control", "step": "100", "placeholder": "19900"}),
            "activo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class ProbarSmtpForm(forms.Form):
    """Formulario de prueba en vivo para despacho de correo SMTP."""
    destinatario = forms.EmailField(
        label="Correo de Prueba Destino",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "tu-correo-personal@gmail.com"}),
    )


class ConfigurarReporteAutomaticoForm(forms.ModelForm):
    """Configuración para el comerciante del despacho periódico automático de reportes en Excel."""
    class Meta:
        model = Establecimiento
        fields = [
            "correo_reportes",
            "reportes_automaticos_activos",
            "frecuencia_reporte_automatico",
        ]
        labels = {
            "correo_reportes": "Correo Electrónico de Recepción",
            "reportes_automaticos_activos": "Activar Envíos Automáticos Periódicos",
            "frecuencia_reporte_automatico": "Frecuencia de Despacho",
        }
        widgets = {
            "correo_reportes": forms.EmailInput(attrs={
                "class": "form-control",
                "placeholder": "mi-negocio@gmail.com o contador@empresa.com",
                "required": True,
            }),
            "reportes_automaticos_activos": forms.CheckboxInput(attrs={
                "class": "form-check-input",
                "style": "transform: scale(1.3); margin-right: 8px;",
            }),
            "frecuencia_reporte_automatico": forms.Select(attrs={
                "class": "form-control",
            }),
        }




