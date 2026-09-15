@echo off
title DiarioComercial - Servidor Local (Puerto 8001)
cd /d "%~dp0"
echo ===================================================
echo   Iniciando DiarioComercial en http://127.0.0.1:8001
echo ===================================================
call .venv\Scripts\activate.bat
python manage.py runserver 8001
pause
