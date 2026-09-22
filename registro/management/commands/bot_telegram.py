"""
Comando de Django para ejecutar el Bot de Telegram de DiarioComercial.

Uso:
  python manage.py bot_telegram --token TU_TELEGRAM_BOT_TOKEN
  O define TELEGRAM_BOT_TOKEN en el archivo .env
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from registro.models import ActividadCIIU, Auditoria, Compra, Establecimiento, Venta


class TelegramClient:
    def __init__(self, token):
        self.base_url = f"https://api.telegram.org/bot{token}"

    def post(self, method, data):
        url = f"{self.base_url}/{method}"
        encoded_data = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=encoded_data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def send_message(self, chat_id, text):
        return self.post("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

    def get_updates(self, offset=None, timeout=25):
        data = {"timeout": timeout}
        if offset is not None:
            data["offset"] = offset
        return self.post("getUpdates", data)


class Command(BaseCommand):
    help = "Inicia el bot de Telegram para capturar ventas y compras en DiarioComercial"

    def add_arguments(self, parser):
        parser.add_argument(
            "--token",
            type=str,
            help="Token secreto del bot entregado por @BotFather",
        )

    def handle(self, *args, **options):
        token = options.get("token") or os.environ.get("TELEGRAM_BOT_TOKEN")

        if not token:
            self.stdout.write(
                self.style.WARNING(
                    "\n[!] No se encontro el token del bot.\n"
                    "Puedes pasarlo asi:\n"
                    "  python manage.py bot_telegram --token TU_TOKEN_DE_BOTFATHER\n"
                )
            )
            try:
                token = input("Por favor pega tu token de Telegram aqui y dale Enter: ").strip()
            except (KeyboardInterrupt, EOFError):
                return

        if not token:
            self.stdout.write(self.style.ERROR("No se proporciono ningún token. Cancelando."))
            return

        client = TelegramClient(token)

        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("  BOT TELEGRAM DIARIOCOMERCIAL INICIADO"))
        self.stdout.write(self.style.SUCCESS("  Escuchando mensajes en tiempo real..."))
        self.stdout.write(self.style.SUCCESS("  (Presiona Ctrl + C para detener)"))
        self.stdout.write(self.style.SUCCESS("=" * 60 + "\n"))

        offset = None

        while True:
            try:
                updates = client.get_updates(offset=offset)
                if not updates.get("ok"):
                    time.sleep(3)
                    continue

                for u in updates.get("result", []):
                    offset = u["update_id"] + 1
                    msg = u.get("message") or u.get("edited_message")
                    if not msg or "text" not in msg:
                        continue

                    chat_id = msg["chat"]["id"]
                    texto = msg["text"].strip()
                    autor = msg.get("from", {}).get("first_name", "Usuario")

                    self.stdout.write(f"Mensaje de {autor}: '{texto}'")
                    respuesta = self.procesar_mensaje(texto, autor)
                    client.send_message(chat_id, respuesta)

            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("\nDeteniendo bot de Telegram..."))
                break
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error en bucle de polling: {e}"))
                time.sleep(2)

    def procesar_mensaje(self, texto, autor):
        texto_limpio = texto.strip()
        cmd = texto_limpio.lower()

        # 1. Comandos de Ayuda y Bienvenida
        if cmd in ("/start", "/ayuda", "ayuda", "hola"):
            return (
                f"👋 *¡Hola {autor}! Asistente DiarioComercial*\n\n"
                "Puedo registrar tus ventas y gastos directamente en el sistema de Tunja.\n\n"
                "📝 *Para registrar una Venta:*\n"
                "`Venta 45000 viveres mostrador`\n"
                "`Venta 120000 carnes empresa`\n\n"
                "🛒 *Para registrar una Compra/Gasto:*\n"
                "`Compra 35000 Distribuidora Boyaca`\n\n"
                "📊 *Para consultar cifras:*\n"
                "`/resumen` — Resumen del día y del mes\n"
                "`/ayuda` — Ver esta guía de ayuda"
            )

        # 2. Comando Resumen
        if cmd in ("/resumen", "resumen", "/saldo", "saldo"):
            return self.generar_resumen()

        # 3. Registrar Venta: "Venta 45000 concepto opcional"
        match_venta = re.match(r"^venta\s+([\d\.,]+)\s*(.*)$", texto_limpio, re.IGNORECASE)
        if match_venta:
            valor_raw = match_venta.group(1).replace(".", "").replace(",", "")
            concepto = match_venta.group(2).strip() or "Venta mostrador Telegram"
            try:
                valor = Decimal(valor_raw)
                return self.guardar_venta(valor, concepto, autor)
            except Exception as e:
                return f"⚠️ Error al procesar el valor: {e}"

        # 4. Registrar Compra: "Compra 80000 proveedor"
        match_compra = re.match(r"^compra\s+([\d\.,]+)\s*(.*)$", texto_limpio, re.IGNORECASE)
        if match_compra:
            valor_raw = match_compra.group(1).replace(".", "").replace(",", "")
            proveedor = match_compra.group(2).strip() or "Gasto registrado por Telegram"
            try:
                valor = Decimal(valor_raw)
                return self.guardar_compra(valor, proveedor, autor)
            except Exception as e:
                return f"⚠️ Error al procesar el valor: {e}"

        # 5. Mensaje no reconocido
        return (
            "❓ No entendí esa instrucción.\n\n"
            "Prueba escribiendo:\n"
            "• `Venta 50000 concepto`\n"
            "• `Compra 25000 proveedor`\n"
            "• `/resumen` para ver el balance"
        )

    def guardar_venta(self, valor, concepto, autor):
        est = Establecimiento.objects.first()
        if not est:
            return "❌ Error: No hay establecimiento configurado en la base de datos."

        usuario = User.objects.filter(username="carlos.ruiz").first() or User.objects.first()
        actividad = ActividadCIIU.objects.filter(establecimiento=est).first()
        tarifa_mil = actividad.tarifa_x_mil if actividad else Decimal("6.0")
        ica = (valor * tarifa_mil) / Decimal("1000")

        venta = Venta.objects.create(
            establecimiento=est,
            usuario=usuario,
            actividad=actividad,
            fecha=date.today(),
            fecha_hora=timezone.now(),
            valor=valor,
            concepto=f"{concepto} (Telegram: {autor})",
            tipo_cliente="particular",
            ica_estimado=ica,
            estado="vigente",
        )

        Auditoria.objects.create(
            usuario=usuario,
            entidad_afectada="venta",
            id_registro=venta.pk,
            accion="crear_telegram",
            valor_nuevo=f"valor={valor}|concepto={concepto}|ica={ica}",
            motivo=f"Venta registrada desde Telegram por {autor}",
        )

        return (
            "✅ *¡Venta registrada con éxito!*\n\n"
            f"🧾 *Comprobante:* #{venta.pk}\n"
            f"💵 *Valor:* ${valor:,.0f} COP\n"
            f"🏷️ *Concepto:* {concepto}\n"
            f"📊 *ICA estimado:* ${ica:,.2f} COP ({tarifa_mil} x mil)\n"
            f"📅 *Fecha:* {date.today().strftime('%d/%m/%Y')}\n\n"
            "_Ya puedes verla reflejada en el navegador de tu PC._"
        )

    def guardar_compra(self, valor, proveedor, autor):
        est = Establecimiento.objects.first()
        if not est:
            return "❌ Error: No hay establecimiento configurado en la base de datos."

        usuario = User.objects.filter(username="carlos.ruiz").first() or User.objects.first()

        compra = Compra.objects.create(
            establecimiento=est,
            usuario=usuario,
            fecha=date.today(),
            valor=valor,
            proveedor=f"{proveedor} (Telegram: {autor})",
            estado="vigente",
        )

        Auditoria.objects.create(
            usuario=usuario,
            entidad_afectada="compra",
            id_registro=compra.pk,
            accion="crear_telegram",
            valor_nuevo=f"valor={valor}|proveedor={proveedor}",
            motivo=f"Compra registrada desde Telegram por {autor}",
        )

        return (
            "🛒 *¡Compra/Gasto registrado!*\n\n"
            f"🧾 *Comprobante:* #{compra.pk}\n"
            f"💵 *Valor:* ${valor:,.0f} COP\n"
            f"🏢 *Proveedor:* {proveedor}\n"
            f"📅 *Fecha:* {date.today().strftime('%d/%m/%Y')}\n\n"
            "_Registrado en el historial de DiarioComercial._"
        )

    def generar_resumen(self):
        est = Establecimiento.objects.first()
        if not est:
            return "❌ No hay establecimiento configurado."

        hoy = date.today()
        primer_dia_mes = hoy.replace(day=1)

        ventas_hoy = Venta.objects.filter(establecimiento=est, fecha=hoy, estado="vigente")
        total_hoy = sum((v.valor for v in ventas_hoy), Decimal("0"))
        ica_hoy = sum((v.ica_estimado for v in ventas_hoy), Decimal("0"))

        ventas_mes = Venta.objects.filter(
            establecimiento=est, fecha__range=(primer_dia_mes, hoy), estado="vigente"
        )
        total_mes = sum((v.valor for v in ventas_mes), Decimal("0"))
        ica_mes = sum((v.ica_estimado for v in ventas_mes), Decimal("0"))

        compras_mes = Compra.objects.filter(
            establecimiento=est, fecha__range=(primer_dia_mes, hoy), estado="vigente"
        )
        total_compras = sum((c.valor for c in compras_mes), Decimal("0"))

        return (
            "📊 *Consolidado DiarioComercial*\n"
            f"📍 _{est.nombre}_\n\n"
            f"☀️ *Hoy ({hoy.strftime('%d/%m/%Y')}):*\n"
            f"  • Ventas: ${total_hoy:,.0f} COP\n"
            f"  • ICA estimado: ${ica_hoy:,.2f} COP\n\n"
            f"📅 *Mes en curso:*\n"
            f"  • Ventas acumuladas: ${total_mes:,.0f} COP\n"
            f"  • Gastos / Compras: ${total_compras:,.0f} COP\n"
            f"  • ICA acumulado: ${ica_mes:,.2f} COP\n"
            f"  • Base neta: ${(total_mes - total_compras):,.0f} COP"
        )
