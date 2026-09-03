import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'production_control.settings')
import django
django.setup()

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site

# Datos de Google
google_client_id = "642670278151-pgkmlqd5ff9kdgkbvfnde5ipd62en84t.apps.googleusercontent.com"
google_secret = "GOCSpX-wTAkHRrg26x7iDUKMfJoz_l8Fudd"

# Obtener o crear la app de Google
app, created = SocialApp.objects.get_or_create(
    provider='google',
    defaults={
        'name': 'Google',
        'client_id': google_client_id,
        'secret': google_secret,
    }
)

if not created:
    app.client_id = google_client_id
    app.secret = google_secret
    app.save()
    print("✓ Datos de Google actualizados")
else:
    print("✓ App de Google creada")

# Agregar el sitio actual
site = Site.objects.get_current()
if not app.sites.filter(pk=site.pk).exists():
    app.sites.add(site)
    print(f"✓ Sitio '{site.domain}' agregado a Google OAuth")

print("\n✓ Google OAuth2 configurado exitosamente!")
print(f"  Client ID: {app.client_id[:20]}...")
print(f"  Sitio: {site.domain}")
