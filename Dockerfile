FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc postgresql-client && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py migrate --noinput --settings=production_control.settings || true
RUN python manage.py collectstatic --noinput --settings=production_control.settings || true

CMD exec gunicorn production_control.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120
