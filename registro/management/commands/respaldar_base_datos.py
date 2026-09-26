"""
Comando de Respaldo Automatizado y Recuperación de Base de Datos para DiarioComercial.
Genera copias de seguridad consistentes con compresión, fecha/hora y rotación automática.
Compatible con PostgreSQL 17 (pg_dump) y fallback nativo Django (dumpdata gz).
"""
import os
import subprocess
import gzip
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.conf import settings
from django.utils import timezone
from registro.models import Auditoria

logger = logging.getLogger("diariocomercial.backup")


class Command(BaseCommand):
    help = "Genera un respaldo completo de la base de datos de DiarioComercial con rotación automática."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            type=str,
            default=str(settings.BASE_DIR / "backups"),
            help="Directorio donde se guardarán los respaldos.",
        )
        parser.add_argument(
            "--retention-days",
            type=int,
            default=30,
            help="Días de retención para eliminar copias antiguas automáticamente (por defecto 30 días).",
        )
        parser.add_argument(
            "--modo",
            type=str,
            choices=["auto", "pg_dump", "django_json"],
            default="auto",
            help="Método de exportación (auto, pg_dump nativo o django_json comprimido).",
        )

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"])
        retention_days = options["retention_days"]
        modo = options["modo"]

        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        db_cfg = settings.DATABASES.get("default", {})
        engine = db_cfg.get("ENGINE", "")
        is_postgres = "postgresql" in engine

        self.stdout.write(f"Iniciando respaldo de base de datos ({timestamp})...")

        archivo_final = None
        tamano_bytes = 0
        metodo_usado = ""

        # Rutas comunes para pg_dump en Windows
        rutas_pg_dump = [
            "pg_dump",
            r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe",
            r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe",
        ]

        ejecutable_pg_dump = None
        for r in rutas_pg_dump:
            if shutil.which(r) or os.path.exists(r):
                ejecutable_pg_dump = r
                break

        usar_pg_dump = is_postgres and ejecutable_pg_dump and (modo in ("auto", "pg_dump"))

        if usar_pg_dump:
            metodo_usado = "PostgreSQL pg_dump (Formato Personalizado Comprimido)"
            nombre_archivo = f"diariocomercial_pg17_{timestamp}.dump"
            ruta_archivo = output_dir / nombre_archivo

            host = db_cfg.get("HOST", "localhost") or "localhost"
            port = str(db_cfg.get("PORT", "5432") or "5432")
            user = db_cfg.get("USER", "postgres") or "postgres"
            dbname = db_cfg.get("NAME", "diariocomercial")
            password = db_cfg.get("PASSWORD", "")

            env_dump = os.environ.copy()
            if password:
                env_dump["PGPASSWORD"] = password

            cmd = [
                ejecutable_pg_dump,
                "-h", host,
                "-p", port,
                "-U", user,
                "-F", "c",          # Formato comprimido oficial de PostgreSQL
                "-b",               # Incluir objetos grandes (blobs)
                "-v",               # Detallado
                "-f", str(ruta_archivo),
                dbname,
            ]

            try:
                self.stdout.write(f"Ejecutando pg_dump hacia {nombre_archivo}...")
                proc = subprocess.run(
                    cmd,
                    env=env_dump,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                archivo_final = ruta_archivo
                tamano_bytes = archivo_final.stat().st_size
                self.stdout.write(self.style.SUCCESS(f"[OK] Respaldo nativo completado: {tamano_bytes / 1024:.2f} KB."))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Aviso: pg_dump falló ({e}). Ejecutando fallback con motor Django..."))
                usar_pg_dump = False

        if not usar_pg_dump or not archivo_final:
            metodo_usado = "Django JSON Serializado (Gzip)"
            nombre_archivo = f"diariocomercial_data_{timestamp}.json.gz"
            ruta_archivo = output_dir / nombre_archivo

            self.stdout.write(f"Generando dumpdata comprimido en {nombre_archivo}...")
            with gzip.open(ruta_archivo, "wt", encoding="utf-8") as gz_file:
                call_command(
                    "dumpdata",
                    "--natural-foreign",
                    "--natural-primary",
                    "--exclude=contenttypes",
                    "--exclude=auth.Permission",
                    stdout=gz_file,
                )
            archivo_final = ruta_archivo
            tamano_bytes = archivo_final.stat().st_size
            self.stdout.write(self.style.SUCCESS(f"[OK] Respaldo comprimido generado: {tamano_bytes / 1024:.2f} KB."))

        # Registro en la tabla de Auditoría
        try:
            Auditoria.objects.create(
                entidad_afectada="base_datos",
                id_registro=0,
                accion="respaldo",
                valor_nuevo=f"Archivo: {archivo_final.name} | Tamaño: {tamano_bytes / 1024:.2f} KB | Método: {metodo_usado}",
                motivo="Respaldo de seguridad preventivo programado",
            )
        except Exception:
            pass

        # Política de Rotación y Limpieza (Retention policy)
        self._limpiar_respaldos_antiguos(output_dir, retention_days)

        self.stdout.write(
            self.style.SUCCESS(
                f"\n=== PROCESO COMPLETADO EXITOSAMENTE ===\n"
                f"Archivo: {archivo_final}\n"
                f"Método: {metodo_usado}\n"
                f"Tamaño: {tamano_bytes / 1024:.2f} KB\n"
                f"Retención activa: {retention_days} días.\n"
            )
        )

    def _limpiar_respaldos_antiguos(self, output_dir: Path, retention_days: int):
        """Elimina respaldos con antigüedad mayor a retention_days para proteger el almacenamiento en disco."""
        limite = timezone.now() - timedelta(days=retention_days)
        eliminados = 0

        for f in output_dir.glob("diariocomercial_*"):
            if f.is_file():
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.get_current_timezone())
                if mtime < limite:
                    try:
                        f.unlink()
                        eliminados += 1
                    except Exception as e:
                        logger.warning(f"No se pudo eliminar respaldo antiguo {f.name}: {e}")

        if eliminados > 0:
            self.stdout.write(f"Limpieza de retención: Se eliminaron {eliminados} copia(s) con más de {retention_days} días.")
