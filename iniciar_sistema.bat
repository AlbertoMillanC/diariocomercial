@echo off
title DiarioComercial - Servidor & Bot Integrado
color 0A
echo ================================================================
echo   DIARIO COMERCIAL - SISTEMA DE GESTION & POS CON BOT
echo ================================================================
echo.
echo [1/3] Verificando base de datos y migraciones...
python manage.py migrate --noinput
echo.
echo [2/3] Iniciando Bot de Telegram en segundo plano...
start /B python -u manage.py bot_telegram
echo.
echo [3/3] Iniciando Servidor Web en http://127.0.0.1:8001/ ...
echo.
echo ================================================================
echo   SISTEMA ACTIVO Y CONECTADO
echo   Plataforma Web: http://127.0.0.1:8001/
echo   Bot de Telegram: @DiarioComercial_bot
echo ================================================================
echo.
python manage.py runserver 127.0.0.1:8001 --noreload
pause
