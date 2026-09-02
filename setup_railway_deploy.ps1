# ============================================
# Script seguro para deploy a Railway
# ============================================

Write-Host "🚀 SETUP PARA RAILWAY CON SUPABASE" -ForegroundColor Green
Write-Host ""
Write-Host "Este script configurará tu aplicación para usar Supabase en Railway"
Write-Host ""

# 1. Pedir credenciales
Write-Host "📋 CREDENCIALES SUPABASE" -ForegroundColor Cyan
Write-Host ""
Write-Host "Ve a https://supabase.com/dashboard/project/[tu-proyecto]/settings/database"
Write-Host "Copia la contraseña de la Base de Datos PostgreSQL"
Write-Host ""

$password = Read-Host "Pega la contraseña de Supabase"

if ([string]::IsNullOrEmpty($password)) {
    Write-Host "❌ Contraseña vacía, abortando..." -ForegroundColor Red
    exit 1
}

# 2. Crear .env.local (no se commitea)
Write-Host ""
Write-Host "💾 Creando .env.local..." -ForegroundColor Cyan

$envContent = @"
# Base de Datos Supabase (NO COMMITEAR ESTE ARCHIVO)
DATABASE_URL=postgresql://postgres:$password@db.akqslrmugubtsseqvvlk.supabase.co:5432/postgres

# Django
DEBUG=False
DJANGO_SETTINGS_MODULE=production_control.settings
SECRET_KEY=produccion-secret-key-cambiar-en-railway
ALLOWED_HOSTS=tu-app.railway.app,localhost
"@

$envContent | Out-File -FilePath ".env.local" -Encoding UTF8 -Force

Write-Host "✅ .env.local creado"
Write-Host ""

# 3. Probar conexión a BD
Write-Host "🔗 Probando conexión a Supabase..." -ForegroundColor Cyan

& ".venv\Scripts\Activate.ps1"

$testScript = @"
import psycopg2
import os

try:
    conn = psycopg2.connect(
        os.environ.get('DATABASE_URL')
    )
    print('✅ Conexión a Supabase OK')
    conn.close()
except Exception as e:
    print(f'❌ Error de conexión: {e}')
    exit(1)
"@

python -c $testScript

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ No se pudo conectar a Supabase" -ForegroundColor Red
    exit 1
}

# 4. Migrar BD
Write-Host ""
Write-Host "🔄 Migrando base de datos..." -ForegroundColor Cyan

python manage.py migrate --settings=production_control.settings

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Error en migraciones" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "✅ Base de datos migrada"
Write-Host ""

# 5. Preparar para Railway
Write-Host "🚀 PRÓXIMOS PASOS PARA RAILWAY:" -ForegroundColor Green
Write-Host ""
Write-Host "1. Ve a https://railway.app"
Write-Host "2. Crea NUEVO proyecto"
Write-Host "3. Conecta tu repositorio GitHub"
Write-Host "4. En el proyecto, añade variable de entorno:"
Write-Host ""
Write-Host "   DATABASE_URL=postgresql://postgres:$password@db.akqslrmugubtsseqvvlk.supabase.co:5432/postgres"
Write-Host ""
Write-Host "5. Railway hará deploy automático"
Write-Host ""
Write-Host "⚠️  IMPORTANTE:"
Write-Host "   - .env.local NO se commitea (ya está en .gitignore)"
Write-Host "   - La contraseña está SOLO en Railway (variable de entorno)"
Write-Host "   - Tus datos en Supabase están SEGUROS y portables"
Write-Host ""
Write-Host "✨ ¡Setup completado!" -ForegroundColor Green
