# Seguridad y respaldos

- Terminar TLS en el proxy o balanceador y activar cookies seguras/HSTS.
- Mantener `.env`, `input/` y `storage/private/` fuera del repositorio público.
- Nginx devuelve 404 para la ruta privada; las descargas pasan por Django y registran usuario, IP y navegador.
- CSRF, validación de servidor, cabeceras seguras, bloqueo temporal de inicio de sesión y límite de carga están activos.
- Ejecutar `scripts/backup.ps1` desde Windows para respaldar PostgreSQL y el volumen privado.
- Probar restauraciones periódicamente y conservar copias cifradas fuera del servidor.
