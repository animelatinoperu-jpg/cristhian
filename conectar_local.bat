@echo off
echo ============================================
echo  MODO: Base de datos local (SQLite)
echo ============================================
echo.
echo Sincronizando desde Railway...
python manage.py sync_railway_to_local --force
echo.
echo Iniciando servidor local...
del .env 2>nul
python manage.py runserver
