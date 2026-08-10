# sync_railway.ps1
# Sincroniza automaticamente las producciones de Railway a la base local SQLite

$ErrorActionPreference = "Continue"
$ProjectDir = "C:\Users\cuent\OneDrive\Desktop\APLICACION DE SOLUCIONES Y PROCESOS MARINOS"
Set-Location $ProjectDir

$LogFile = Join-Path $ProjectDir "sync_railway.log"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

Add-Content -Path $LogFile -Value ""
Add-Content -Path $LogFile -Value "[$timestamp] Iniciando sincronizacion Railway -> Local..."

$output = python manage.py sync_railway_to_local --force 2>&1
$output | ForEach-Object { Add-Content -Path $LogFile -Value $_ }

Add-Content -Path $LogFile -Value "[$timestamp] Finalizado."
