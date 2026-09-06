from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from registro.models import (
    ActividadCIIU,
    Compra,
    Establecimiento,
    MotivoVenta,
    Perfil,
    Retencion,
    Venta,
)


class Command(BaseCommand):
    help = "Crea un minimarket de prueba con CIIU, motivos, propietario y dependiente"

    def handle(self, *args, **options):
        est, _ = Establecimiento.objects.get_or_create(
            nombre="Minimarket El Parque",
            defaults={
                "nit": "900123456",
                "direccion": "Tunja",
                "actividad_economica": "Comercio al por menor",
                "correo_reportes": "contador.tunja@correo.com",
            },
        )
        maria, created_m = User.objects.get_or_create(
            username="maria.gomez",
            defaults={"first_name": "María", "last_name": "Gómez", "email": "maria@local"},
        )
        maria.set_password("tunja2026")
        maria.is_staff = True
        maria.is_superuser = True
        maria.save()
        Perfil.objects.get_or_create(
            user=maria, defaults={"establecimiento": est, "rol": "propietario"}
        )
        carlos, created_c = User.objects.get_or_create(
            username="carlos.ruiz",
            defaults={"first_name": "Carlos", "last_name": "Ruiz"},
        )
        if created_c:
            carlos.set_password("tunja2026")
            carlos.save()
        Perfil.objects.get_or_create(
            user=carlos, defaults={"establecimiento": est, "rol": "dependiente"}
        )

        c4711, _ = ActividadCIIU.objects.get_or_create(
            establecimiento=est,
            codigo="4711",
            defaults={
                "descripcion": "Comercio al por menor de alimentos y víveres",
                "tarifa_x_mil": Decimal("6.00"),
            },
        )
        c4723, _ = ActividadCIIU.objects.get_or_create(
            establecimiento=est,
            codigo="4723",
            defaults={
                "descripcion": "Comercio al por menor de carnes",
                "tarifa_x_mil": Decimal("6.00"),
            },
        )
        MotivoVenta.objects.get_or_create(
            establecimiento=est,
            actividad=c4711,
            nombre="Venta de víveres",
            defaults={"es_predeterminado": True},
        )
        MotivoVenta.objects.get_or_create(
            establecimiento=est,
            actividad=c4723,
            nombre="Venta de carne",
            defaults={"es_predeterminado": True},
        )
        for v in Venta.objects.filter(establecimiento=est, actividad__isnull=True):
            v.actividad = c4711
            v.save()

        ahora = timezone.localtime()
        if not Venta.objects.filter(establecimiento=est, tipo_cliente="empresa").exists():
            v1 = Venta.objects.create(
                establecimiento=est,
                usuario=carlos,
                actividad=c4711,
                motivo=MotivoVenta.objects.get(actividad=c4711, nombre="Venta de víveres"),
                fecha=ahora.date(),
                fecha_hora=ahora,
                valor=Decimal("420000"),
                concepto="Venta de víveres",
                tipo_cliente="particular",
                estado="vigente",
            )
            v2 = Venta.objects.create(
                establecimiento=est,
                usuario=carlos,
                actividad=c4723,
                motivo=MotivoVenta.objects.get(actividad=c4723, nombre="Venta de carne"),
                fecha=(ahora - timedelta(days=1)).date(),
                fecha_hora=ahora - timedelta(days=1),
                valor=Decimal("390000"),
                concepto="Venta de carne",
                tipo_cliente="particular",
                estado="vigente",
            )
            v3 = Venta.objects.create(
                establecimiento=est,
                usuario=maria,
                actividad=c4711,
                motivo=MotivoVenta.objects.get(actividad=c4711, nombre="Venta de víveres"),
                fecha=ahora.date(),
                fecha_hora=ahora - timedelta(hours=2),
                valor=Decimal("1500000"),
                concepto="Venta de víveres",
                tipo_cliente="empresa",
                estado="vigente",
            )
            Retencion.objects.create(
                establecimiento=est,
                usuario=maria,
                venta=v3,
                fecha=v3.fecha,
                tipo="ica",
                valor=Decimal("12400"),
                tercero="Cliente mayorista SAS",
                estado="vigente",
            )
            Compra.objects.create(
                establecimiento=est,
                usuario=maria,
                fecha=ahora.date(),
                valor=Decimal("180000"),
                proveedor="Abarrotes Boyacá",
                concepto="Mercancía",
                estado="vigente",
            )
        self.stdout.write(self.style.SUCCESS("Listo. maria.gomez y carlos.ruiz / tunja2026"))
