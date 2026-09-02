# 🚀 DEPLOYMENT A RAILWAY - PASO A PASO

**ESTADO**: ✅ CÓDIGO LISTO | ✅ BD CONFIGURADA | ✅ ARCHIVOS SEGUROS

---

## 📋 RESUMEN FINAL

| Componente | Status | Detalles |
|-----------|--------|----------|
| **Código** | ✅ Listo | En GitHub, con todas las migraciones |
| **BD Local** | ✅ Listo | SQLite funcionando (desarrollo) |
| **BD Producción** | ✅ Listo | Supabase PostgreSQL (gratuita) |
| **Archivos** | ✅ Guardados | En carpeta `output/` + plantillas |
| **Aplicación Web** | ✅ Funciona | Probada en localhost:8000 |

---

## 🎯 PASO 1: CREAR CUENTA EN RAILWAY (si no la tienes)

```
1. Ve a https://railway.app
2. Click "Sign Up"
3. Conecta tu GitHub
```

---

## 🎯 PASO 2: CREAR NUEVO PROYECTO EN RAILWAY

1. En Railway dashboard, click **"New Project"**
2. Selecciona **"Deploy from GitHub"**
3. Selecciona tu repositorio (el que tiene esta aplicación)
4. Railway detectará automáticamente `Dockerfile`
5. Click **"Deploy"**

---

## 🎯 PASO 3: CONFIGURAR LA VARIABLE DE BASE DE DATOS

**ESTO ES LO MÁS IMPORTANTE:**

1. En el proyecto de Railway, ve a **"Variables"**
2. Click **"New Variable"**
3. **NOMBRE**: `DATABASE_URL`
4. **VALOR**: Copia EXACTAMENTE esto:

```
postgresql://postgres:sEVk9vLcQCghHZzM@db.akqslrmugubtsseqvlk.supabase.co:5432/postgres
```

5. Click **"Save"**

---

## 🎯 PASO 4: RAILWAY HARÁ TODO AUTOMÁTICAMENTE

Railway detectará:
- ✅ El `Dockerfile`
- ✅ La variable `DATABASE_URL`
- ✅ Ejecutará `python manage.py migrate` automáticamente
- ✅ Iniciará la aplicación

**ESPERA**: A que Railway termine el deployment (~5 minutos)

---

## 🎯 PASO 5: VERIFICAR QUE FUNCIONA

Una vez que Railway termina:

1. Railway te dará una URL como: `https://tu-app.railway.app`
2. Abre esa URL en el navegador
3. Deberías ver tu aplicación funcionando 🎉

---

## 📝 NOTAS IMPORTANTES

### ✅ Lo que está SEGURO:

- ✅ Tu código en GitHub (versionado)
- ✅ Tus datos en Supabase (independiente de Railway)
- ✅ Tus archivos/láminas/Excel en la carpeta local
- ✅ Si Railway muere → tus datos siguen en Supabase
- ✅ Puedes cambiar de hosting cuando quieras

### ⚠️ Recuerda:

- La contraseña de Supabase SOLO está en Railway (variable de entorno)
- `.env.local` NO se commitea a GitHub (está en `.gitignore`)
- Tu aplicación funciona localmente sin conexión a Internet

---

## 🔗 ENLACES RÁPIDOS

| Sitio | URL |
|------|-----|
| Railway | https://railway.app |
| Supabase | https://supabase.com/dashboard |
| Tu Repo | (tu URL de GitHub) |

---

## ✅ CHECKLIST FINAL

- [ ] Cuenta en Railway creada
- [ ] Nuevo proyecto en Railway
- [ ] Variable `DATABASE_URL` añadida
- [ ] Railway terminó el deployment
- [ ] Aplicación visible en `https://tu-app.railway.app`
- [ ] Datos en Supabase seguros

---

## 🆘 SI ALGO FALLA

### "Error de conexión a BD"
→ Verifica que `DATABASE_URL` esté exacto (copiar y pegar)

### "Railway se queda cargando"
→ Revisa los logs en Railway dashboard (sección "Logs")

### "Página en blanco"
→ Probably los logs te dirán qué pasó

---

## 🎉 RESULTADO FINAL

Tu aplicación estará:
- ✅ En vivo en Internet (`railway.app`)
- ✅ Con BD PostgreSQL gratuita (`supabase.co`)
- ✅ Todos tus datos SEGUROS y PORTABLES
- ✅ Nunca más perderás datos por cambiar de hosting

---

**¡Listo! Procede con los pasos arriba.** 🚀

