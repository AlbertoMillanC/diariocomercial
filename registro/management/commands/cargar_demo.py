from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from registro.models import Compra, Establecimiento, Perfil, Retencion, Venta


class Command(BaseCommand):
    help = "Crea un minimarket de prueba con propietario y dependiente"

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
        if created_m:
            maria.set_password("tunja2026")
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
        hoy = date.today()
        if not Venta.objects.filter(establecimiento=est).exists():
            Venta.objects.create(
                establecimiento=est, usuario=carlos, fecha=hoy,
                valor=Decimal("420000"), concepto="Ventas de mostrador", estado="vigente",
            )
            Venta.objects.create(
                establecimiento=est, usuario=carlos, fecha=hoy - timedelta(days=1),
                valor=Decimal("390000"), concepto="Ventas del día", estado="vigente",
            )
            Compra.objects.create(
                establecimiento=est, usuario=maria, fecha=hoy,
                valor=Decimal("180000"), proveedor="Abarrotes Boyacá",
                concepto="Mercancía", estado="vigente",
            )
            Retencion.objects.create(
                establecimiento=est, usuario=maria, fecha=hoy - timedelta(days=1),
                tipo="ica", valor=Decimal("12400"), tercero="Cliente mayorista", estado="vigente",
            )
        self.stdout.write(self.style.SUCCESS("Listo. Usuarios: maria.gomez y carlos.ruiz / tunja2026"))
