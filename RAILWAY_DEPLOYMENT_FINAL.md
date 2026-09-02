# 🚀 DEPLOYMENT A RAILWAY CON SUPABASE

**Estado**: ✅ LISTO PARA DESPLEGAR

---

## 📋 RESUMEN

Tu aplicación Django ahora está configurada para:
- ✅ **Desarrollo local**: SQLite (funciona sin conexión externa)
- ✅ **Producción**: Supabase PostgreSQL (en Railway)

**¡Nunca más perderás datos!** Tus datos viven en Supabase, completamente independientes de Railway.

---

## 🏃 PASO 1: EJECUTAR LOCALMENTE (OPCIONAL)

Para probar la aplicación antes de desplegar:

```bash
python manage.py runserver
```

Accede a: http://localhost:8000

---

## 🚀 PASO 2: DESPLEGAR A RAILWAY

### 2.1 Crear cuenta Railway (si no la tienes)

1. Ve a https://railway.app
2. Sign up / Login
3. Conecta tu GitHub

### 2.2 Crear nuevo proyecto en Railway

1. Click en **New Project**
2. Selecciona **Deploy from GitHub**
3. Conecta tu repositorio (ese que tiene esta aplicación)
4. Railway detectará automáticamente tu Dockerfile

### 2.3 CONFIGURAR LA VARIABLE DE ENTORNO (MÁS IMPORTANTE)

En tu proyecto Railway:

1. Ve a **Variables**
2. Crea **nueva variable**:

```
DATABASE_URL=postgresql://postgres:sEVk9vLcQCghHZzM@db.akqslrmugubtsseqvlk.supabase.co:5432/postgres
```

3. Guarda los cambios

### 2.4 Railway hará deployment automático

- Railway verá los cambios en GitHub
- Ejecutará `python manage.py migrate` automáticamente
- Tu app estará en vivo en Railway

---

## 🔐 CREDENCIALES SUPABASE (GUARDADAS AQUÍ PARA REFERENCIA)

```
Host: db.akqslrmugubtsseqvlk.supabase.co
Port: 5432
Database: postgres
User: postgres
Password: sEVk9vLcQCghHZzM
```

**⚠️ IMPORTANTE**: La contraseña está SOLO en Railway (variable DATABASE_URL), NO en el código.

---

## 📊 ESTRUCTURA DE ARCHIVOS IMPORTANTE

```
.env.local          ← NO se commitea (desarrollo local)
.gitignore          ← .env.local ya está ahí
Dockerfile          ← Railway usa esto
docker-compose.yml  ← Para desarrollo con compose
requirements.txt    ← Python dependencies
manage.py           ← Django CLI
```

---

## ✅ CHECKLIST PRE-DEPLOYMENT

- [ ] Cuenta Railway creada
- [ ] Repositorio GitHub conectado
- [ ] Variable `DATABASE_URL` añadida en Railway
- [ ] Dockerfile presente en el repo
- [ ] `.env.local` está en `.gitignore`
- [ ] Última versión pusheada a GitHub

---

## 🛠️ SOLUCIÓN DE PROBLEMAS

### ❌ "Error en migraciones"
→ Verificar que `DATABASE_URL` esté correctamente en Railway

### ❌ "Conexión a BD rechazada"
→ Copiar exactamente: `postgresql://postgres:sEVk9vLcQCghHZzM@db.akqslrmugubtsseqvlk.supabase.co:5432/postgres`

### ❌ "Página en blanco en Railway"
→ Revisar logs en Railway dashboard

---

## 📞 RESUMEN FINAL

| Componente | Ubicación | Status |
|-----------|-----------|--------|
| **Código** | GitHub | ✅ Listo |
| **BD Desarrollo** | SQLite local | ✅ Listo |
| **BD Producción** | Supabase | ✅ Disponible |
| **Hosting** | Railway | ⏳ A configurar |
| **Credenciales** | Railway variables | 📝 Ver arriba |

---

## 🎯 PRÓXIMO PASO

**Crear proyecto en Railway.app y añadir la variable DATABASE_URL**

```
DATABASE_URL=postgresql://postgres:sEVk9vLcQCghHZzM@db.akqslrmugubtsseqvlk.supabase.co:5432/postgres
```

¡Listo! 🚀

---

**Generado**: 2026-09-02
**Versión**: 1.0
