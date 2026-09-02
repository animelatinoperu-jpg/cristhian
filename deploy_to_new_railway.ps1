# ============================================
# Script para desplegar a nueva cuenta Railway
# ============================================

Write-Host "🚀 Railway Migration Script" -ForegroundColor Green
Write-Host "=============================" -ForegroundColor Green
Write-Host ""

# Verificar que Git está disponible
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Git no está instalado" -ForegroundColor Red
    exit 1
}

# Verificar que Railway CLI está disponible
if (-not (Get-Command railway -ErrorAction SilentlyContinue)) {
    Write-Host "⚠️  Railway CLI no encontrado. Descárgalo de:" -ForegroundColor Yellow
    Write-Host "   https://docs.railway.app/develop/cli" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Instalación rápida (requiere npm):"
    Write-Host "   npm install -g @railway/cli" -ForegroundColor Yellow
    Write-Host ""

    $response = Read-Host "¿Deseas continuar sin Railway CLI? (s/n)"
    if ($response -ne "s") {
        exit 1
    }
}

Write-Host "📋 Checklist antes de continuar:" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. ¿Has creado un nuevo proyecto en Railway? (s/n)" -ForegroundColor Yellow
$newProject = Read-Host
if ($newProject -ne "s") {
    Write-Host "   👉 Crea uno en https://railway.app" -ForegroundColor Cyan
    exit 1
}

Write-Host ""
Write-Host "2. ¿Has creado una base de datos PostgreSQL en Railway? (s/n)" -ForegroundColor Yellow
$hasDB = Read-Host
if ($hasDB -ne "s") {
    Write-Host "   👉 Añade PostgreSQL en el dashboard de tu proyecto" -ForegroundColor Cyan
    exit 1
}

Write-Host ""
Write-Host "3. ¿Has copiado las variables de entorno (POSTGRES_URL_NON_POOLING)? (s/n)" -ForegroundColor Yellow
$hasEnv = Read-Host
if ($hasEnv -ne "s") {
    Write-Host "   👉 Ve a Settings → Variables en tu proyecto de Railway" -ForegroundColor Cyan
    exit 1
}

Write-Host ""
Write-Host "✓ Verificando estado del repositorio..." -ForegroundColor Green

# Verificar que todo está limpio
$status = git status --short
if ($status) {
    Write-Host "⚠️  Hay cambios sin guardar:" -ForegroundColor Yellow
    Write-Host $status
    $confirm = Read-Host "¿Hacer commit de estos cambios? (s/n)"

    if ($confirm -eq "s") {
        git add -A
        git commit -m "chore: preparar para deploy a nueva Railway

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
    } else {
        Write-Host "Cancela commit pendientes antes de continuar" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "🔑 Autenticar con Railway..." -ForegroundColor Cyan
Write-Host "   (Se abrirá tu navegador para que inicies sesión en tu nueva cuenta)" -ForegroundColor Gray
Write-Host ""

railway login

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Autenticación fallida" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "📦 Selecciona tu nuevo proyecto en Railway..." -ForegroundColor Cyan
railway link

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ No se pudo vincular el proyecto" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "📤 Desplegando..." -ForegroundColor Green

# Hacer push al repositorio de Railway
# Railway detectará los cambios automáticamente
Write-Host ""
Write-Host "✓ Tu repositorio está vinculado a la nueva cuenta de Railway" -ForegroundColor Green
Write-Host ""
Write-Host "📝 Próximos pasos:" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Ve al dashboard de Railway: https://railway.app/dashboard" -ForegroundColor Yellow
Write-Host "2. Selecciona tu nuevo proyecto" -ForegroundColor Yellow
Write-Host "3. Asegúrate de que las variables de entorno están configuradas" -ForegroundColor Yellow
Write-Host "4. El despliegue debería iniciar automáticamente" -ForegroundColor Yellow
Write-Host ""
Write-Host "Para ver los logs en tiempo real:" -ForegroundColor Cyan
Write-Host "   railway logs" -ForegroundColor Gray
Write-Host ""
Write-Host "Para ejecutar migraciones de Django:" -ForegroundColor Cyan
Write-Host "   railway run python manage.py migrate --settings=production_control.settings" -ForegroundColor Gray
Write-Host ""

Write-Host "✅ ¡Migración completada!" -ForegroundColor Green
