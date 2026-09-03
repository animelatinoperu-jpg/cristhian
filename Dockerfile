FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc postgresql-client && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput --settings=production_control.settings || true

CMD ["gunicorn", "production_control.wsgi:application", "--bind", "0.0.0.0:8000"]
