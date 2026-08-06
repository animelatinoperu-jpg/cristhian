# Partes de Producción

Aplicación Django multiusuario para registrar la producción por áreas, conciliar bandejas y empaques, auditar cambios y generar una copia segura de la plantilla macrohabilitada desde PostgreSQL.

## Estado verificable de la plantilla

En el workspace recibido no existe `input/PLANTILLA_PP_V1.xlsm`. El proyecto no inventa coordenadas: `config/excel_mapping_v1.yaml` queda bloqueado y el botón de generación devuelve un mensaje seguro. Los documentos de Fase 0 registran este hallazgo.

Cuando el archivo real esté disponible en esa ruta, ejecutar:

```powershell
.\.venv\Scripts\python.exe manage.py analyze_template --copy-private --register-user admin --version PP-V1
```

El comando inventariará todas las hojas visibles/ocultas, fórmulas, celdas sin fórmula, rangos combinados, protecciones, dependencias, nombres definidos, VBA, comentarios, dibujos, imágenes, errores y áreas de impresión. Las celdas aparecen primero como candidatas no autorizadas. Tras la revisión funcional, cambiar el mapa a `status: validated`, asociar cada `field` y conservar el SHA-256 exacto.

## Windows 10 — desarrollo

1. Instalar Python 3.12 y PostgreSQL 16, o Docker Desktop.
2. Crear el entorno e instalar dependencias:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo --username admin --password "Cambiar-Esta-Clave-123"
.\.venv\Scripts\python.exe manage.py runserver
```

Por defecto el desarrollo local usa SQLite. Para PostgreSQL, definir `DB_ENGINE=postgresql` y las variables `POSTGRES_*`.

## Docker y producción

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose exec web python manage.py analyze_template --copy-private --register-user admin --version PP-V1
```

Configurar HTTPS delante de Nginx y después activar `SECURE_SSL_REDIRECT=1`. La plantilla y los archivos generados viven en un volumen privado; Nginx no los sirve.

## Flujo

1. El jefe crea un PP y fija lote, turno y versión de plantilla.
2. Asigna usuarios por área y, para supervisores, por túnel.
3. Cada área registra datos de catálogo; el lote nunca se vuelve a pedir.
4. El sistema concilia racks contra cuadrillas y plaqueros contra cuadrillas, y ofrece un reporte consolidado en línea/A4.
5. El jefe observa, aprueba y cierra. Las diferencias requieren justificación.
6. Al generar, Django consulta PostgreSQL, escribe únicamente celdas mapeadas, fuerza recálculo y valida integridad antes de habilitar la descarga.

## Pruebas

```powershell
.\.venv\Scripts\python.exe manage.py test -v 2
```

Las 20 pruebas cubren autenticación, permisos por túnel y turno, concurrencia optimista, llenadas 1/2, capacidades, P1/P2/P3, conciliación, duplicados, PDF y preservación de VBA/fórmulas/celdas no autorizadas con una fixture XLSM aislada.

## Documentación

- `docs/INVENTARIO_HOJAS.md`
- `docs/MAPA_CELDAS.md`
- `docs/FLUJO_ENTRE_HOJAS.md`
- `docs/INCONSISTENCIAS_EXCEL.md`
- `docs/ARQUITECTURA.md`
- `docs/BASE_DE_DATOS.md`
- `docs/ROLES_Y_PERMISOS.md`
- `docs/SEGURIDAD_Y_RESPALDOS.md`
