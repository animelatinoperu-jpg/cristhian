param(
    [string]$OutputDirectory = ".\backups"
)

$ErrorActionPreference = "Stop"
$resolvedRoot = (Resolve-Path -LiteralPath ".").Path
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$resolvedOutput = (Resolve-Path -LiteralPath $OutputDirectory).Path
if (-not $resolvedOutput.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "El directorio de respaldo debe estar dentro del proyecto."
}
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$databaseFile = Join-Path $resolvedOutput "postgres_$stamp.sql"
$storageFile = Join-Path $resolvedOutput "private_storage_$stamp.tar.gz"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | Set-Content -LiteralPath $databaseFile -Encoding utf8
docker compose run --rm --no-deps -v "${resolvedOutput}:/backup" web tar -czf "/backup/private_storage_$stamp.tar.gz" -C /app/storage private
Write-Host "Base de datos: $databaseFile"
Write-Host "Archivos privados: $storageFile"
