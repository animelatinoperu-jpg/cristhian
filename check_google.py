import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'production_control.settings')
import django
django.setup()

from allauth.socialaccount.models import SocialApp
apps = SocialApp.objects.all()
if apps.exists():
    for app in apps:
        print(f"✓ Provider: {app.provider}")
        print(f"  Name: {app.name}")
        print(f"  Client ID: {app.client_id}")
else:
    print("✗ No hay aplicaciones sociales configuradas")
    print("\nNecesitas:")
    print("1. Ir a http://localhost:8000/admin/")
    print("2. Ir a 'Social applications'")
    print("3. Agregar Google con Client ID y Secret")
