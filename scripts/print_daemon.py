#!/usr/bin/env python3
"""
DiarioComercial - Agente Local de Impresión Térmica POS (Print Daemon)
======================================================================
Agente de fondo (Background Spooler) para impresoras térmicas de 58mm y 80mm
(USB, Red/Ethernet, Bluetooth y Puertos COM en Windows/Linux).

Funcionamiento:
  1. Sondea periódicamente (polling) la cola de impresión de DiarioComercial:
     GET /api/pedidos/cola-impresion/?token=<TOKEN>
  2. Despacha comandos ESC/POS nativos directamente al hardware (impresión,
     corte automático de papel y activación de buzzer/timbre de aviso).
  3. Envía acuse de recibo inmutable (ACK):
     POST /api/pedidos/ack-impresion/<ID>/
     para evitar cualquier duplicidad física de comandas en la cocina o mostrador.

Uso:
  python scripts/print_daemon.py --token <TOKEN_COMERCIO> --printer-name "POS-80"
  python scripts/print_daemon.py --token <TOKEN_COMERCIO> --mode console (Modo prueba)
"""

import argparse
import base64
import json
import logging
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("PrintDaemon")


def enviar_a_impresora_win32(printer_name: str, raw_bytes: bytes, doc_name: str = "Comanda POS") -> bool:
    """Envía bytes ESC/POS en crudo al spooler de Windows vía win32print."""
    try:
        import win32print
    except ImportError:
        logger.error("Módulo 'pywin32' no instalado. Ejecute: pip install pywin32 para imprimir en Windows.")
        return False

    try:
        hprinter = win32print.OpenPrinter(printer_name)
        try:
            hjob = win32print.StartDocPrinter(hprinter, 1, (doc_name, None, "RAW"))
            try:
                win32print.StartPagePrinter(hprinter)
                win32print.WritePrinter(hprinter, raw_bytes)
                win32print.EndPagePrinter(hprinter)
            finally:
                win32print.EndDocPrinter(hprinter)
        finally:
            win32print.ClosePrinter(hprinter)
        return True
    except Exception as e:
        logger.error(f"Error escribiendo en impresora Windows '{printer_name}': {e}")
        return False


def enviar_a_impresora_red(ip: str, port: int, raw_bytes: bytes, timeout: int = 5) -> bool:
    """Envía bytes ESC/POS directamente a una impresora térmica de red (TCP 9100)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((ip, port))
            s.sendall(raw_bytes)
        return True
    except Exception as e:
        logger.error(f"Error conectando a impresora de red {ip}:{port} - {e}")
        return False


def consultar_cola(server_url: str, token: str) -> dict:
    """Consulta la API de cola de impresión del servidor Django."""
    url = f"{server_url.rstrip('/')}/api/pedidos/cola-impresion/?token={urllib.parse.quote(token)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DiarioComercial-PrintDaemon/1.0",
            "X-Printer-Token": token,
        }
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        if response.status == 200:
            return json.loads(response.read().decode("utf-8"))
    return {}


def confirmar_impresion_ack(server_url: str, token: str, pedido_id: int) -> bool:
    """Envía el ACK al servidor para marcar el pedido como impreso."""
    url = f"{server_url.rstrip('/')}/api/pedidos/ack-impresion/{pedido_id}/"
    data = json.dumps({"token": token, "timestamp": time.time()}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DiarioComercial-PrintDaemon/1.0",
            "X-Printer-Token": token,
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200
    except Exception as e:
        logger.error(f"Error enviando ACK para pedido #{pedido_id}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="DiarioComercial - POS Thermal Print Daemon")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="URL base del servidor Django")
    parser.add_argument("--token", required=True, help="Token de vinculación o NIT del establecimiento")
    parser.add_argument("--printer-name", default="POS-80", help="Nombre de la impresora en Windows")
    parser.add_argument("--printer-ip", help="IP de la impresora si es de red (ej: 192.168.1.200)")
    parser.add_argument("--printer-port", type=int, default=9100, help="Puerto de red de la impresora")
    parser.add_argument("--poll-interval", type=float, default=3.0, help="Intervalo de consulta en segundos")
    parser.add_argument("--mode", choices=["win32", "network", "console"], default="win32", help="Modo de salida")
    args = parser.parse_args()

    logger.info("==================================================================")
    logger.info("🚀 DiarioComercial - Agente de Impresión Térmica POS Iniciado")
    logger.info(f"   Servidor:   {args.server}")
    logger.info(f"   Modo:       {args.mode.upper()}")
    if args.mode == "win32":
        logger.info(f"   Impresora:  {args.printer_name}")
    elif args.mode == "network":
        logger.info(f"   IP Red:     {args.printer_ip}:{args.printer_port}")
    logger.info(f"   Intervalo:  {args.poll_interval}s")
    logger.info("==================================================================")

    consecutive_errors = 0
    while True:
        try:
            data = consultar_cola(args.server, args.token)
            consecutive_errors = 0
            trabajos = data.get("trabajos", [])
            est_nombre = data.get("establecimiento", "Comercio")

            if trabajos:
                logger.info(f"📥 [{est_nombre}] {len(trabajos)} comanda(s) pendiente(s) recibida(s).")

            for t in trabajos:
                pedido_id = t["id"]
                numero_ped = t["numero_pedido"]
                cliente = t["cliente"]
                direccion = t["direccion"]
                escpos_b64 = t.get("escpos_base64", "")
                raw_bytes = base64.b64decode(escpos_b64) if escpos_b64 else b""
                texto_termico = t.get("texto_termico", "")

                impreso_ok = False
                if args.mode == "console":
                    print("\n" + "=" * 45)
                    print(f"🖨️ MOCK IMPRESIÓN POS: COMANDA #{numero_ped:04d}")
                    print("=" * 45)
                    print(texto_termico)
                    print("=" * 45 + "\n")
                    impreso_ok = True
                elif args.mode == "network" and args.printer_ip:
                    impreso_ok = enviar_a_impresora_red(args.printer_ip, args.printer_port, raw_bytes)
                else:
                    impreso_ok = enviar_a_impresora_win32(args.printer_name, raw_bytes, doc_name=f"Comanda_{numero_ped}")

                if impreso_ok:
                    logger.info(f"✅ Pedido #{numero_ped:04d} ({cliente} - {direccion}) impreso físicamente.")
                    ack_ok = confirmar_impresion_ack(args.server, args.token, pedido_id)
                    if ack_ok:
                        logger.info(f"   ↳ ACK confirmado para pedido #{numero_ped:04d}.")
                    else:
                        logger.warning(f"   ⚠️ ACK no pudo enviarse para pedido #{numero_ped:04d}.")
                else:
                    logger.error(f"❌ Falló impresión de comanda #{numero_ped:04d}. Se reintentará...")

        except urllib.error.URLError as e:
            consecutive_errors += 1
            if consecutive_errors == 1 or consecutive_errors % 20 == 0:
                logger.warning(f"Error de conexión con {args.server}: {e.reason}. Reintentando...")
        except Exception as e:
            logger.error(f"Excepción en ciclo de impresión: {e}")

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
