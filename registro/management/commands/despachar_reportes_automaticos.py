"""
Comando de Django para despachar reportes periódicos automáticos en Excel a los correos de los comercios.
Puede ejecutarse periódicamente mediante un cron job o programador de tareas de Windows.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from registro.email_service import enviar_correo_plataforma
from registro.models import EnvioReporte, Establecimiento
from registro.reporte_excel_service import generar_excel_reporte_periodico

logger = logging.getLogger("diariocomercial.reportes_automaticos")


class Command(BaseCommand):
    help = "Evalúa y despacha los reportes periódicos automáticos en Excel según la frecuencia de cada comercio"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Fuerza el envío inmediato ignorando el intervalo de tiempo transcurrido",
        )
        parser.add_argument(
            "--establecimiento-id",
            type=int,
            default=None,
            help="Filtra por un establecimiento específico",
        )
        parser.add_argument(
            "--frecuencia",
            type=str,
            default=None,
            help="Filtra por frecuencia específica ('hora', 'turno', 'diario', 'semanal', 'mensual')",
        )

    def handle(self, *args, **options):
        force = options["force"]
        est_id = options["establecimiento_id"]
        frec_filtro = options["frecuencia"]

        ahora = timezone.now()
        hoy = timezone.localdate()

        qs = Establecimiento.objects.filter(
            reportes_automaticos_activos=True,
            correo_reportes__isnull=False,
        ).exclude(correo_reportes="").exclude(frecuencia_reporte_automatico="desactivado")

        if est_id:
            qs = qs.filter(pk=est_id)
        if frec_filtro:
            qs = qs.filter(frecuencia_reporte_automatico=frec_filtro)

        self.stdout.write(f"[{ahora.strftime('%Y-%m-%d %H:%M:%S')}] Evaluando {qs.count()} comercio(s) activos...")

        enviados = 0
        omitidos = 0
        errores = 0

        for est in qs:
            frec = est.frecuencia_reporte_automatico
            ultimo = est.ultimo_reporte_automatico
            es_elegible = force or self._es_elegible_por_tiempo(frec, ultimo, ahora)

            if not es_elegible:
                omitidos += 1
                continue

            desde, hasta, titulo = self._calcular_rango_periodo(frec, hoy, ahora)

            self.stdout.write(f"Generando reporte para '{est.nombre}' ({frec}) -> {est.correo_reportes}...")

            try:
                excel_bytes, nombre_archivo = generar_excel_reporte_periodico(
                    est=est,
                    desde=desde,
                    hasta=hasta,
                    titulo_periodo=titulo,
                )

                asunto = f"Reporte Automatico de Operaciones [{titulo}] - {est.nombre}"
                mensaje = (
                    f"Estimado(a) comerciante,\n\n"
                    f"Adjunto encontrará su reporte automático de operaciones en formato Excel (.xlsx) "
                    f"correspondiente al periodo: {titulo} ({desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}).\n\n"
                    f"Contenido del reporte adjunto:\n"
                    f"• Hoja 1: Resumen Ejecutivo y Arqueo de Caja por Medio de Pago.\n"
                    f"• Hoja 2: Detalle cronológico de Ventas y Comprobantes.\n"
                    f"• Hoja 3: Registro de Compras y Gastos Operativos.\n"
                    f"• Hoja 4: Inventario Actual, existencias valorizadas y márgenes.\n\n"
                    f"Comercio: {est.nombre}\n"
                    f"NIT: {est.nit or 'No registrado'}\n"
                    f"Frecuencia configurada: {est.get_frecuencia_reporte_automatico_display()}\n\n"
                    f"Este informe se genera y respalda automáticamente desde la plataforma DiarioComercial."
                )

                adjuntos = [
                    (
                        nombre_archivo,
                        excel_bytes,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                ]

                exito = enviar_correo_plataforma(
                    asunto=asunto,
                    mensaje=mensaje,
                    destinatarios=[est.correo_reportes],
                    adjuntos=adjuntos,
                )

                usuario_responsable = est.propietario_creador
                if not usuario_responsable and est.perfil_set.exists():
                    usuario_responsable = est.perfil_set.first().user
                if not usuario_responsable:
                    usuario_responsable = User.objects.filter(is_superuser=True).first() or User.objects.first()

                if usuario_responsable:
                    from django.db.models import Sum
                    from registro.models import Compra, Retencion, Venta
                    tot_v = Venta.objects.filter(establecimiento=est, fecha__range=(desde, hasta), estado="vigente").aggregate(t=Sum("valor"))["t"] or Decimal("0")
                    tot_c = Compra.objects.filter(establecimiento=est, fecha__range=(desde, hasta), estado="vigente").aggregate(t=Sum("valor"))["t"] or Decimal("0")
                    tot_ica = Venta.objects.filter(establecimiento=est, fecha__range=(desde, hasta), estado="vigente").aggregate(t=Sum("ica_estimado"))["t"] or Decimal("0")

                    EnvioReporte.objects.create(
                        establecimiento=est,
                        usuario=usuario_responsable,
                        periodo_inicio=desde,
                        periodo_fin=hasta,
                        correo_destino=est.correo_reportes,
                        estado_envio="automatico" if exito else "fallido",
                        total_ingresos=tot_v,
                        total_egresos=tot_c,
                        total_retenciones=Decimal("0"),
                        total_ica=tot_ica,
                    )

                est.ultimo_reporte_automatico = ahora
                est.save(update_fields=["ultimo_reporte_automatico"])

                self.stdout.write(self.style.SUCCESS(f"[OK] Reporte despachado exitosamente a {est.correo_reportes} ({nombre_archivo})"))
                enviados += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"[ERROR] Error despachando reporte para {est.nombre}: {e}"))
                logger.error(f"Fallo en reporte automático {est.nombre}: {e}", exc_info=True)
                errores += 1

        self.stdout.write(
            f"Resumen del proceso: {enviados} enviados, {omitidos} en espera de intervalo, {errores} errores."
        )

    def _es_elegible_por_tiempo(self, frecuencia, ultimo, ahora):
        if not ultimo:
            return True

        delta = ahora - ultimo

        if frecuencia == "hora":
            return delta >= timedelta(minutes=55)
        elif frecuencia == "turno":
            return delta >= timedelta(hours=11, minutes=45)
        elif frecuencia == "diario":
            return delta >= timedelta(hours=23) or (ahora.date() > ultimo.date())
        elif frecuencia == "semanal":
            return delta >= timedelta(days=6, hours=20)
        elif frecuencia == "mensual":
            return delta >= timedelta(days=27) or (ahora.month != ultimo.month)
        return False

    def _calcular_rango_periodo(self, frecuencia, hoy, ahora):
        if frecuencia == "hora":
            return hoy, hoy, "Última Hora"
        elif frecuencia == "turno":
            return hoy, hoy, "Cierre de Turno (12h)"
        elif frecuencia == "diario":
            return hoy, hoy, "Cierre Diario"
        elif frecuencia == "semanal":
            inicio_sem = hoy - timedelta(days=hoy.weekday())
            return inicio_sem, hoy, "Consolidado Semanal"
        elif frecuencia == "mensual":
            inicio_mes = hoy.replace(day=1)
            return inicio_mes, hoy, "Consolidado Mensual"
        return hoy, hoy, "Reporte General"
