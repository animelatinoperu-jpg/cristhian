@echo off
echo ============================================
echo  MODO: Base de datos Railway (online)
echo ============================================
echo.
copy /Y .env.railway .env >nul
python manage.py runserver
