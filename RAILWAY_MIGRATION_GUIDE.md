# 🚀 Guía de Migración a Nueva Cuenta Railway

**Fecha:** 2026-09-02  
**Estado:** Listo para migrar

---

## 📋 Lo que hemos hecho

✅ **Guardado en Git:**
- Todos los cambios del proyecto están en el commit `00bd5ac`
- Historial completo de commits preservado
- Rama `master` actualizada

✅ **Información de la BD actual:**
- **Host:** altaria.proxy.rlwy.net
- **Puerto:** 17907
- **Base de datos:** railway
- **Usuario:** postgres

---

## 🔄 Pasos para migrar a la nueva cuenta Railway

### Paso 1: Crear nuevo proyecto en Railway
1. Inicia sesión en tu **nueva cuenta** de Railway (https://railway.app)
2. Crea un nuevo proyecto: `New Project` → `Deploy from GitHub` o `Create a new service`
3. Si es desde Git, selecciona este repositorio

### Paso 2: Configurar variable de entorno
En el dashboard de Railway (nueva cuenta), ve a tu proyecto y añade estas variables:

```
DJANGO_SETTINGS_MODULE=production_control.settings
DEBUG=False
ALLOWED_HOSTS=tu-dominio-en-railway.railway.app
SECRET_KEY=tu-secret-key-seguro
DB_ENGINE=postgresql
POSTGRES_HOST=tu-nuevo-host-railway.rlwy.net
POSTGRES_PORT=tu-puerto
POSTGRES_DB=railway
POSTGRES_USER=postgres
POSTGRES_PASSWORD=tu-nueva-contraseña
```

### Paso 3: Crear servicio PostgreSQL
1. En Railway, añade un servicio nuevo: `Add Service` → `Database` → `PostgreSQL`
2. Railway te dará automáticamente las variables de conexión
3. Copia `POSTGRES_URL_NON_POOLING` en tus variables de entorno

### Paso 4: Migrar datos (si es necesario)
Primero, ejecuta las migraciones en la nueva BD:
```bash
python manage.py migrate --settings=production_control.settings
```

Si tienes datos importantes, puedes hacer un dump de la BD antigua y restaurar en la nueva.

### Paso 5: Deploy
```bash
git push railway master
```

O simplemente haz push a la rama que está conectada en Railway.

---

## 📦 Archivos importantes de tu proyecto

### Configuración
- `railway.toml` - Configuración de Railway
- `.env.railway` - Variables de entorno (⚠️ NO commitear secretos)
- `Dockerfile` - Construcción de la imagen Docker
- `docker-compose.yml` - Para desarrollo local
- `Procfile` - Comando para iniciar la app

### Aplicación Django
- `production_control/settings.py` - Configuración de Django
- `manage.py` - CLI de Django
- `requirements.txt` - Dependencias de Python
- `templates/` - Plantillas HTML
- `static/` - Archivos estáticos

### Datos
- `db.sqlite3` - BD local (no usar en Railway)
- `backups/` - Backups de la BD (si los creaste)
- `storage/` - Almacenamiento de archivos

---

## 🔐 Seguridad - Variables sensibles

**NUNCA commiteés:**
- `.env` (usa `.env.example` en su lugar)
- Contraseñas o API keys en el código
- Credenciales de base de datos

**Para Railroad:** Usa las variables de entorno que Railway proporciona automáticamente.

---

## ✅ Checklist de migración

- [ ] Nueva cuenta de Railway creada
- [ ] Nuevo proyecto en Railway inicializado
- [ ] PostgreSQL creado en la nueva cuenta
- [ ] Variables de entorno configuradas
- [ ] `git push` realizado
- [ ] Build completado en Railway
- [ ] Migraciones de Django ejecutadas
- [ ] Sitio funcionando correctamente

---

## 📞 Ayuda rápida

**Si hay errores de conexión a BD:**
```bash
python manage.py dbshell --settings=production_control.settings
```

**Para ver logs en Railway:**
```bash
railway logs
```

**Para resetear BD local:**
```bash
rm db.sqlite3
python manage.py migrate
```

---

**Archivo guardado:** 2026-09-02 10:12 UTC  
**Proyecto:** carpeta railway  
**Branch:** master
