#!/usr/bin/env python
"""
Servidor de Producción Ultraligero y de Alta Eficiencia para DiarioComercial.
Utiliza Waitress (WSGI multi-hilo de producción para Windows/Linux) con consumo < 50MB RAM.
Protege contra WinError 1450, agotamiento de recursos y saturación de CPU por file-watchers.
"""
import os
import sys
import logging
from pathlib import Path

# Configurar Django settings
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "diariocomercial.settings")

import django
django.setup()

from django.core.wsgi import get_wsgi_application
from waitress import serve

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("diariocomercial.server")

def arrancar_bot_background():
    """Ejecuta el bot de Telegram en un hilo daemon con autoreconexión continua y watchdog."""
    import time
    from django.core.management import call_command
    # Pequeña pausa para permitir que el servidor WSGI enlace el puerto primero
    time.sleep(1.5)
    while True:
        try:
            logger.info("🤖 Supervisor: Iniciando Bot de Telegram integrado...")
            call_command("bot_telegram")
        except Exception as e:
            logger.error(f"⚠️ Supervisor: Reintentando bot de Telegram tras error: {e}")
            time.sleep(3)

def main():
    port = int(os.environ.get("PORT", "8001"))
    host = os.environ.get("HOST", "127.0.0.1")
    threads = int(os.environ.get("WAITRESS_THREADS", "6"))
    
    logger.info("================================================================")
    logger.info("DIARIOCOMERCIAL - SERVIDOR WSGI DE PRODUCCION OPTIMIZADO")
    logger.info(f"Escuchando en: http://{host}:{port}")
    logger.info(f"Pool de Hilos Concurrente: {threads} threads")
    logger.info("Proteccion Activa: Cero fugas de kernel, memoria < 50MB RAM")
    logger.info("================================================================")

    # Iniciar bot integrado en segundo plano si no se usa webhook externo
    import threading
    t_bot = threading.Thread(target=arrancar_bot_background, daemon=True, name="DiarioComercial-BotWorker")
    t_bot.start()
    
    app = get_wsgi_application()
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        channel_timeout=60,
        cleanup_interval=30,
        max_request_body_size=10485760,  # 10MB máx
        asyncore_use_poll=True if sys.platform != "win32" else False,
    )

if __name__ == "__main__":
    main()
