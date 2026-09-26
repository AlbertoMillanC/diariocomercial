import sys
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from registro.bot_service import despachar_mensaje, generar_token_vinculacion
from registro.models import Establecimiento, VinculoCanal


class Command(BaseCommand):
    help = "Simulador interactivo o directo de mensajes entrantes de WhatsApp para DiarioComercial"

    def add_arguments(self, parser):
        parser.add_argument("telefono", nargs="?", default="573146922087", help="Número de teléfono celular (WhatsApp)")
        parser.add_argument("mensaje", nargs="?", default="", help="Texto del mensaje a simular")
        parser.add_argument("--interactivo", action="store_true", help="Inicia sesión interactiva de chat por WhatsApp")
        parser.add_argument("--vincular", action="store_true", help="Vincula automáticamente el número al primer establecimiento si no lo está")

    def handle(self, *args, **options):
        # Asegurar UTF-8 en terminal Windows
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass

        telefono = str(options["telefono"]).strip().replace("+", "")
        if len(telefono) == 10:
            telefono = f"57{telefono}"

        if options["vincular"]:
            est = Establecimiento.objects.first()
            if est and est.propietario_creador:
                VinculoCanal.objects.update_or_create(
                    canal="whatsapp",
                    identificador_externo=telefono,
                    defaults={
                        "usuario": est.propietario_creador,
                        "establecimiento": est,
                        "activo": True,
                    }
                )
                self.stdout.write(self.style.SUCCESS(f"✅ Teléfono {telefono} vinculado a {est.nombre}"))

        if options["interactivo"]:
            self.stdout.write(self.style.SUCCESS("=" * 60))
            self.stdout.write(self.style.SUCCESS(f"📱 CONSOLA DE PRUEBAS WHATSAPP DIARIOCOMERCIAL"))
            self.stdout.write(self.style.SUCCESS(f"   Número Emisor: +{telefono}"))
            self.stdout.write(self.style.SUCCESS("   (Escribe 'salir' para terminar)"))
            self.stdout.write(self.style.SUCCESS("=" * 60))

            while True:
                try:
                    texto = input("\n[WhatsApp] Tu > ").strip()
                    if not texto or texto.lower() in ("salir", "exit", "quit"):
                        break
                    resp, venta, adj = despachar_mensaje(
                        canal="whatsapp",
                        identificador_externo=telefono,
                        texto_mensaje=texto,
                        return_adjuntos=True,
                    )
                    self.stdout.write(f"\n[DiarioComercial] Bot >\n{resp}")
                    if venta:
                        self.stdout.write(self.style.NOTICE(f"\n⚡ Venta registrada en BD: ID #{venta.pk} • {venta.concepto} • ${venta.valor:,.0f} COP ({venta.medio_pago})"))
                except (KeyboardInterrupt, EOFError):
                    break
            self.stdout.write(self.style.SUCCESS("\nSesión de prueba WhatsApp finalizada."))
            return

        mensaje = options["mensaje"]
        if not mensaje:
            self.stdout.write(self.style.WARNING("Uso: python manage.py simular_whatsapp <telefono> <mensaje>"))
            self.stdout.write(self.style.WARNING("Ejemplo: python manage.py simular_whatsapp 3146922087 \"40 mil carne nequi\""))
            self.stdout.write(self.style.NOTICE("O para modo chat: python manage.py simular_whatsapp --interactivo"))
            return

        self.stdout.write(self.style.NOTICE(f"\n📲 Enviando mensaje simulado desde WhatsApp (+{telefono}): '{mensaje}'\n"))
        resp, venta, adj = despachar_mensaje(
            canal="whatsapp",
            identificador_externo=telefono,
            texto_mensaje=mensaje,
            return_adjuntos=True,
        )
        self.stdout.write(self.style.SUCCESS(f"Respuesta del Asistente:\n{resp}\n"))
        if venta:
            self.stdout.write(self.style.SUCCESS(f"✅ Venta registrada en Base de Datos:"))
            self.stdout.write(f"   • Ticket: #{venta.pk:05d}")
            self.stdout.write(f"   • Concepto: {venta.concepto}")
            self.stdout.write(f"   • Valor: ${venta.valor:,.0f} COP")
            self.stdout.write(f"   • Medio de pago: {venta.medio_pago}")
            self.stdout.write(f"   • Cantidad: {venta.cantidad} {venta.unidad_medida}")
