#!/usr/bin/env python3
"""
Conectar a BD PostgreSQL de Railway y hacer backup de los datos
"""
import os
import json
import sys
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("❌ psycopg2 no instalado, instalando...")
    os.system("pip install psycopg2-binary -q")
    import psycopg2
    from psycopg2.extras import RealDictCursor

# Credenciales
dbHost = "altaria.proxy.rlwy.net"
dbPort = "17907"
dbName = "railway"
dbUser = "postgres"
dbPassword = "YyqKMYpUPhAiZcBLqBYBikujSLDwspYB"

print("🔄 Conectando a BD de Railway...")
print(f"   Host: {dbHost}:{dbPort}")
print(f"   BD: {dbName}")
print("")

try:
    # Conectar
    conn = psycopg2.connect(
        host=dbHost,
        port=dbPort,
        database=dbName,
        user=dbUser,
        password=dbPassword,
        sslmode="require"
    )

    print("✅ Conexión exitosa!")
    print("")

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Obtener listado de tablas
    cursor.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)

    tables = [row['table_name'] for row in cursor.fetchall()]
    print(f"📊 Tablas encontradas: {len(tables)}")
    for table in tables:
        print(f"   - {table}")

    # Hacer dump SQL completo
    print("")
    print("💾 Creando backup SQL...")

    # Usar pg_dump via Python (si está disponible en el sistema)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = f"backups/railway_dump_{timestamp}.sql"

    os.makedirs("backups", exist_ok=True)

    # Crear dump usando subprocess
    import subprocess
    env = os.environ.copy()
    env['PGPASSWORD'] = dbPassword

    result = subprocess.run([
        'pg_dump',
        '-h', dbHost,
        '-p', dbPort,
        '-U', dbUser,
        '-d', dbName,
        '--no-password'
    ], env=env, capture_output=True, text=True)

    if result.returncode == 0:
        with open(backup_file, 'w') as f:
            f.write(result.stdout)
        size_mb = os.path.getsize(backup_file) / 1024 / 1024
        print(f"✅ Backup SQL creado: {backup_file}")
        print(f"   Tamaño: {size_mb:.2f} MB")
    else:
        raise Exception("pg_dump falló")

    # Hacer dump JSON también
    print("")
    print("📋 Exportando datos como JSON...")

    all_data = {}
    for table in tables:
        cursor.execute(f"SELECT * FROM {table}")
        rows = cursor.fetchall()
        all_data[table] = [dict(row) for row in rows]
        print(f"   ✓ {table}: {len(rows)} registros")

    json_file = f"backups/railway_data_{timestamp}.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, indent=2, default=str)

    size_mb = os.path.getsize(json_file) / 1024 / 1024
    print(f"✅ JSON creado: {json_file}")
    print(f"   Tamaño: {size_mb:.2f} MB")

    cursor.close()
    conn.close()

    print("")
    print("🎉 ¡Backup completado exitosamente!")
    print("")
    print("Archivos creados:")
    print(f"  1. {backup_file} (SQL completo)")
    print(f"  2. {json_file} (Datos JSON)")

except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
