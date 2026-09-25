import json
from pathlib import Path
from django.core.management.base import BaseCommand
from registro.models import Municipio


class Command(BaseCommand):
    help = "Carga masiva de los 1.122 municipios de Colombia oficiales del DANE (DIVIPOLA)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limpiar",
            action="store_true",
            help="Limpiar tabla de municipios antes de cargar (cuidado con FKs existentes)",
        )

    def handle(self, *args, **options):
        json_path = Path(__file__).resolve().parent.parent.parent / "data" / "municipios_colombia_dane.json"
        if not json_path.exists():
            self.stderr.write(self.style.ERROR(f"No se encontró el archivo de datos: {json_path}"))
            return

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.stdout.write(f"Iniciando carga de {len(data)} municipios DANE...")

        # Mapeo existente por codigo_dane
        existentes = {m.codigo_dane: m for m in Municipio.objects.all()}
        nuevos = []
        actualizados = 0

        for item in data:
            cod = item["codigo_dane"]
            nom = item["nombre"]
            dep = item["departamento"]

            if cod in existentes:
                mun = existentes[cod]
                if mun.nombre != nom or mun.departamento != dep or not mun.activo:
                    mun.nombre = nom
                    mun.departamento = dep
                    mun.activo = True
                    mun.save(update_fields=["nombre", "departamento", "activo"])
                    actualizados += 1
            else:
                nuevos.append(
                    Municipio(
                        codigo_dane=cod,
                        nombre=nom,
                        departamento=dep,
                        activo=True,
                    )
                )

        if nuevos:
            Municipio.objects.bulk_create(nuevos, batch_size=500)

        total = Municipio.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Proceso completado con éxito: {len(nuevos)} municipios creados, {actualizados} actualizados. Total en base de datos: {total} municipios."
            )
        )
