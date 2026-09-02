#!/usr/bin/env python
"""
Sincroniza archivos Excel y PDF generados del directorio output/
a la base de datos de Django como GeneratedFile records.
"""
import os
import hashlib
import django
from pathlib import Path
from datetime import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'production_control.settings')
django.setup()

from django.contrib.auth import get_user_model
from productions.models import GeneratedFile, ProductionOrder, TemplateVersion

User = get_user_model()

# Obtener o crear usuario admin
admin_user, _ = User.objects.get_or_create(
    username='admin',
    defaults={'email': 'admin@marinos.com', 'is_staff': True, 'is_superuser': True}
)

# Obtener o crear PP de ejemplo
pp, _ = ProductionOrder.objects.get_or_create(
    number=999,
    defaults={
        'plant_lot': 'TEST-001',
        'customer_lot': 'CUST-001',
        'production_date': datetime.now().date(),
        'reception_date': datetime.now().date(),
        'shift': 'M',
        'status': 'C'
    }
)

# Obtener la primera template version
template = TemplateVersion.objects.first()
if not template:
    print("❌ No hay TemplateVersion en la BD. Crea una primero.")
    exit(1)

# Carpeta de output
output_dir = Path('output')
files_synced = 0
files_skipped = 0

print("🔄 Sincronizando archivos...")
print(f"   Template: {template.code}")
print(f"   PP: {pp.number}")
print()

# Procesar archivos Excel
for file_path in sorted(output_dir.glob('*.xlsx')):
    filename = file_path.name

    # Saltar si ya existe
    if GeneratedFile.objects.filter(filename=filename).exists():
        print(f"⏭️  {filename} (ya existe)")
        files_skipped += 1
        continue

    # Calcular SHA256
    sha256_hash = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for byte_block in iter(lambda: f.read(4096), b''):
            sha256_hash.update(byte_block)

    # Crear registro
    with open(file_path, 'rb') as f:
        gen_file = GeneratedFile(
            production=pp,
            template_version=template,
            kind='FINAL',
            filename=filename,
            sha256=sha256_hash.hexdigest(),
            generated_by=admin_user,
            valid=True
        )
        gen_file.file.save(filename, f)
        gen_file.save()

    print(f"✅ {filename}")
    files_synced += 1

# Procesar archivos PDF
pdf_dir = output_dir / 'pdf'
if pdf_dir.exists():
    for file_path in sorted(pdf_dir.glob('*.pdf')):
        filename = file_path.name

        if GeneratedFile.objects.filter(filename=filename).exists():
            print(f"⏭️  {filename} (ya existe)")
            files_skipped += 1
            continue

        sha256_hash = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for byte_block in iter(lambda: f.read(4096), b''):
                sha256_hash.update(byte_block)

        with open(file_path, 'rb') as f:
            gen_file = GeneratedFile(
                production=pp,
                template_version=template,
                kind='FINAL',
                filename=filename,
                sha256=sha256_hash.hexdigest(),
                generated_by=admin_user,
                valid=True
            )
            gen_file.file.save(filename, f)
            gen_file.save()

        print(f"✅ {filename}")
        files_synced += 1

print()
print(f"📊 Resultado:")
print(f"   ✅ Sincronizados: {files_synced}")
print(f"   ⏭️  Saltados: {files_skipped}")
print()
print(f"🌐 Ver en: http://localhost:8000/admin/productions/generatedfile/")
