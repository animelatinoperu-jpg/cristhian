#!/bin/bash
set -e

echo "Running Django checks..."
python manage.py check --settings=production_control.settings

echo "Running migrations..."
python manage.py migrate --noinput --settings=production_control.settings || true

echo "Collecting static files..."
python manage.py collectstatic --noinput --settings=production_control.settings || true

echo "Starting gunicorn..."
exec gunicorn production_control.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120 --access-logfile - --error-logfile -
