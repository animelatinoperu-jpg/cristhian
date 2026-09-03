# Google OAuth2 Setup

Google Sign-In ya está configurado en tu aplicación. Solo necesitas agregar tus credenciales.

## Pasos para obtener las credenciales:

1. Ve a [Google Cloud Console](https://console.cloud.google.com)
2. Crea un nuevo proyecto o selecciona uno existente
3. Habilita la API de Google+
4. Ve a "Credenciales" > "Crear credencial" > "ID de cliente OAuth"
5. Selecciona "Aplicación web"
6. Agrega estos URIs autorizados:
   - `http://localhost:8000`
   - `http://localhost:8000/cuentas/google/login/callback/`
   - Tu dominio en producción (ej: `https://tudominio.com`)
7. Copia el **Client ID** y **Client Secret**

## Agregar credenciales a tu aplicación:

### Opción 1: Usar el admin de Django

1. Inicia sesión en `http://localhost:8000/admin/`
2. Ve a "Social applications" (Aplicaciones sociales)
3. Clic en "Add Social Application"
4. Llena los datos:
   - **Provider**: Google
   - **Name**: Google
   - **Client id**: [Tu Google Client ID]
   - **Secret key**: [Tu Google Client Secret]
   - **Sites**: Selecciona tu sitio
5. Guarda

### Opción 2: Variables de entorno

Agrega a tu `.env`:
```
GOOGLE_CLIENT_ID=tu_client_id
GOOGLE_CLIENT_SECRET=tu_client_secret
```

Luego crea un script Python para configurar desde env:
```python
from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site

site = Site.objects.get_current()
google_app, created = SocialApp.objects.get_or_create(
    provider='google',
    defaults={
        'name': 'Google',
        'client_id': os.getenv('GOOGLE_CLIENT_ID'),
        'secret': os.getenv('GOOGLE_CLIENT_SECRET'),
    }
)
if not google_app.sites.filter(pk=site.pk).exists():
    google_app.sites.add(site)
```

## Probar

1. Ve a `http://localhost:8000/cuentas/login/`
2. Haz clic en "Ingresar con Google"
3. ¡Listo!

**Nota**: Los usuarios nuevos que se autentican con Google se crearán automáticamente en tu sistema.
