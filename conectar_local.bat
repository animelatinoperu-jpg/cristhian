@echo off
echo ============================================
echo  MODO: Base de datos local (SQLite)
echo ============================================
echo.
del .env 2>nul
python manage.py runserver
