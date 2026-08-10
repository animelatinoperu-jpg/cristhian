release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: gunicorn production_control.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 3 --timeout 120
