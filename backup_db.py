#!/usr/bin/env python
"""
Script para hacer backup de la base de datos Django
"""
import os
import sys
import django
from datetime import datetime
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.management import call_command
from django.db import connection

# Crear directorio de backups
backup_dir = Path('backups')
backup_dir.mkdir(exist_ok=True)

timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')

try:
    # Exportar datos con dumpdata
    fixture_file = backup_dir / f'db_data_{timestamp}.json'
    print(f"📦 Exportando datos de Django a {fixture_file}...")

    with open(fixture_file, 'w') as f:
        call_command('dumpdata', stdout=f, indent=2)

    print(f"✓ Datos exportados ({fixture_file.stat().st_size / 1024 / 1024:.2f} MB)")

    # Verificar conexión y obtener info
    print("\n📊 Información de la base de datos:")
    with connection.cursor() as cursor:
        # Contar tablas
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'public'
        """)
        table_count = cursor.fetchone()[0]
        print(f"   Tablas: {table_count}")

        # Listar tablas
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        print("   - " + "\n   - ".join([row[0] for row in cursor.fetchall()]))

    print("\n✓ Backup completado exitosamente")
    sys.exit(0)

except Exception as e:
    print(f"\n❌ Error: {e}")
    sys.exit(1)
