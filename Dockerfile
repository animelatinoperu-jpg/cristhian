FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc postgresql-client && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py migrate --noinput --settings=production_control.settings 2>&1 || true
RUN python manage.py collectstatic --noinput --settings=production_control.settings 2>&1 || true

EXPOSE 8000

CMD ["sh", "-c", "python manage.py check --settings=production_control.settings && gunicorn production_control.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1 --timeout 120 --access-logfile - --error-logfile -"]
